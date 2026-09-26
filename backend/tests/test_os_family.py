"""OS family from OS name (v2.421.0).

Only nmap supplied a family; Nessus, NetExec and operator corrections give a
name only, so most hosts on a Nessus-heavy inventory had none.
"""
import pytest

from app.db import models
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.os_family import os_family_from_name


@pytest.mark.parametrize("name,family", [
    ("Microsoft Windows Server 2019 Standard", "Windows"),
    ("Microsoft Windows 10 Enterprise\nMicrosoft Windows 11", "Windows"),
    ("Windows Server 2022 Build 20348 x64", "Windows"),
    ("Server 2019 Standard 17763", "Windows"),           # NetExec before v2.421.0
    ("10 Build 19041 x64", "Windows"),                     # NetExec before v2.421.0
    ("Linux Kernel 3.10 on CentOS Linux release 7", "Linux"),
    ("Ubuntu Linux 22.04", "Linux"),
    ("Debian 12", "Linux"),
    ("Cisco IOS 15.2", "IOS"),
    ("Cisco NX-OS Version 9.3", "NX-OS"),
    ("Cisco Adaptive Security Appliance (ASA) 9.16", "ASA"),
    ("VMware ESXi 7.0", "ESXi"),
    ("Citrix NetScaler 13.1", "NetScaler"),
    ("FreeBSD 13.2", "FreeBSD"),
    ("Mac OS X 10.15", "macOS"),
    ("Juniper Junos OS 21.4", "JUNOS"),
    ("FortiOS 7.2 on Fortinet FortiGate", "FortiOS"),
    ("Unix - Samba", None),
    ("Synthetic retained down-host fixture", None),
    ("", None),
    (None, None),
])
def test_family_from_name(name, family):
    assert os_family_from_name(name) == family


def test_netexec_keeps_windows_in_the_os_name(db_session, test_project, tmp_path):
    from app.parsers.netexec_parser import NetexecParser
    p = tmp_path / "nxc.txt"
    p.write_text("SMB         10.63.1.5       445    DC09    [*] Windows Server 2019 Standard 17763 x64 "
                 "(name:DC09) (domain:corp.example) (signing:True) (SMBv1:False)\n")
    NetexecParser(db_session).parse_file(str(p), p.name, project_id=test_project.id)
    host = db_session.query(models.Host).filter(
        models.Host.project_id == test_project.id, models.Host.ip_address == "10.63.1.5").one()
    assert host.os_name.startswith("Windows Server 2019")
    assert host.os_family == "Windows"


def test_a_nessus_host_gets_the_family_and_nmap_s_is_kept(db_session, test_project):
    scan = models.Scan(filename="x", tool_name="nessus", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    dedup = HostDeduplicationService(db_session)
    host = dedup.find_or_create_host("10.63.0.1", scan.id, {"state": "up", "os_name": "Microsoft Windows Server 2016"},
                                     project_id=test_project.id)
    assert host.os_family == "Windows"
    # A scanner's own family is never replaced by a derived one.
    other = dedup.find_or_create_host("10.63.0.2", scan.id,
                                      {"state": "up", "os_name": "Linux 5.X", "os_family": "embedded"},
                                      project_id=test_project.id)
    assert other.os_family == "embedded"
