"""Imports that say what they did and did not read (v2.417.0, review R03/R12).

BloodHound and RustScan kept no parse stats: a file with nothing usable
"processed successfully" with zero hosts, BloodHound objects without an
address vanished silently, and RustScan's embedded nmap report looked
imported when only the open ports were.
"""
import json

import pytest

from app.db import models
from app.parsers.bloodhound_parser import BloodHoundParser
from app.parsers.rustscan_parser import RustScanParser


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_bloodhound_counts_the_computers_it_could_not_place(db_session, test_project, tmp_path):
    p = _write(tmp_path, "computers.json", json.dumps({"computers": [
        {"Properties": {"name": "WS01.CORP.LOCAL", "ipv4": "10.8.0.11"}},
        {"Properties": {"name": "WS02.CORP.LOCAL"}},
    ]}))
    parser = BloodHoundParser(db_session)
    parser.parse_file(str(p), p.name, project_id=test_project.id)
    stats = parser.last_parse_stats
    assert stats["skipped"] == 1 and stats["partial"] is True
    assert "WS02.CORP.LOCAL" in stats["warnings"]
    assert "not imported" in stats["summary"]
    assert db_session.query(models.Host).filter(
        models.Host.project_id == test_project.id, models.Host.ip_address == "10.8.0.11").count() == 1


def test_bloodhound_with_no_addressed_computer_is_not_a_success(db_session, test_project, tmp_path):
    p = _write(tmp_path, "users.json", json.dumps({"data": [
        {"Properties": {"name": "ALICE@CORP.LOCAL"}}, {"Properties": {"name": "BOB@CORP.LOCAL"}},
    ]}))
    with pytest.raises(ValueError, match="no computer with an IPv4 address"):
        BloodHoundParser(db_session).parse_file(str(p), p.name, project_id=test_project.id)


def test_rustscan_with_no_open_port_lines_is_not_a_success(db_session, test_project, tmp_path):
    p = _write(tmp_path, "rustscan.txt", ".----. .-. .-. RustScan\nNothing found.\n")
    with pytest.raises(ValueError, match="No RustScan open-port lines"):
        RustScanParser(db_session).parse_file(str(p), p.name, project_id=test_project.id)


def test_rustscan_says_its_embedded_nmap_report_was_not_read(db_session, test_project, tmp_path):
    p = _write(tmp_path, "rustscan.txt", (
        "Open 10.8.0.20:22\nOpen 10.8.0.20:80\n"
        "Nmap scan report for 10.8.0.20\n"
        "PORT   STATE SERVICE REASON  VERSION\n"
        "22/tcp open  ssh     syn-ack OpenSSH 8.9\n"
    ))
    parser = RustScanParser(db_session)
    parser.parse_file(str(p), p.name, project_id=test_project.id)
    stats = parser.last_parse_stats
    assert stats["summary"] == "2 open ports on 1 host"
    assert "nmap's report" in stats["warnings"]


def test_rustscan_without_nmap_output_has_no_warning(db_session, test_project, tmp_path):
    p = _write(tmp_path, "rustscan.txt", "Open 10.8.0.21:443\n")
    parser = RustScanParser(db_session)
    parser.parse_file(str(p), p.name, project_id=test_project.id)
    assert parser.last_parse_stats["warnings"] is None
