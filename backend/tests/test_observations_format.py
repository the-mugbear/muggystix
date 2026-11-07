"""The tool-agnostic observation format (2.537.0; the owner, 2026-11-06: the
platform must take future tools' output and an agent's own).

One JSON object per observation, every field the uploader's explicit
statement — the normalization rule made writable from outside:

* recognised by its ``schema`` key, first among the JSON formats, never by
  the filename;
* severity absent → UNKNOWN; ``exploitable`` absent or null → NULL; ``cvss``
  → score + vector with the version derived by the writer; every CVE kept;
* ``check_id`` names a catalog check (the catalog's title and severity);
* a line with no host address, a severity word that is not one, a port out of
  range or a non-boolean ``exploitable`` is skipped and counted, never
  guessed and never the whole file;
* rows carry ``source = external``; the tool is the first line's ``tool``,
  else the upload's ``tool_name``, else ``external``.
The every-format contracts (hosts touched, a cut file, renamed hosts) hold it
through ``CASES`` in ``test_every_import_records_the_hosts_it_touches.py``.
"""
from __future__ import annotations

import json

import pytest

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilityCve, VulnerabilitySeverity
from app.parsers.content_detection import looks_like_observations
from app.parsers.observations_parser import ObservationsParser
from app.services.format_registry import detect

LINES = [
    {"schema": "bluestick-observation/1", "tool": "homebrew-scanner", "host": "10.92.0.1",
     "hostname": "web01.example.test", "port": 443, "protocol": "tcp", "service": "https",
     "title": "Outdated TLS library", "severity": "High", "description": "d", "solution": "s",
     "references": ["https://example.test/a"],
     "cvss": {"score": 9.8, "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"},
     "cves": ["cve-2024-0001", "CVE-2024-0002", "not-a-cve"], "exploitable": True,
     "plugin_id": "tls-1", "evidence": "openssl 1.0.1", "observed_at": "2026-11-06T10:00:00Z"},
    {"schema": "bluestick-observation/1", "host": "10.92.0.2", "title": "Default community string",
     "port": 161, "protocol": "udp"},
    {"schema": "bluestick-observation/1", "host": "10.92.0.2", "title": "Host-level note", "severity": "info",
     "exploitable": False},
    {"schema": "bluestick-observation/1", "host": "10.92.0.2", "check_id": "smb_signing_not_required",
     "port": 445, "evidence": "signing: False"},
    # Not placeable or not statements: skipped and counted.
    {"schema": "bluestick-observation/1", "hostname": "only-a-name.example.test", "title": "No address"},
    {"schema": "bluestick-observation/1", "host": "10.92.0.3", "title": "Bad severity", "severity": "scary"},
    {"schema": "bluestick-observation/1", "host": "10.92.0.3", "title": "Bad flag", "exploitable": "yes"},
    {"schema": "bluestick-observation/1", "host": "10.92.0.3", "title": "Bad port", "port": 70000},
    {"schema": "bluestick-observation/1", "host": "10.92.0.3", "check_id": "no_such_check"},
]


def _write(tmp_path, records, name="obs.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def test_recognised_by_its_schema_key_whatever_the_filename(tmp_path):
    sample = ("\n".join(json.dumps(r) for r in LINES[:2]) + "\n").encode()
    assert looks_like_observations(sample, "whatever.txt")
    assert looks_like_observations(json.dumps(LINES[:2]).encode(), "export.json")
    assert not looks_like_observations(json.dumps([{"template-id": "x", "info": {}}]).encode(), "observations.json")
    first = detect("anything.jsonl", sample)[0]
    assert (first[0], first[1].__name__) == ("observations_jsonl", "ObservationsParser")


def test_every_field_is_the_uploaders_statement(db_session, test_project, tmp_path):
    path = _write(tmp_path, LINES)
    parser = ObservationsParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=test_project.id)

    assert scan.tool_name == "homebrew-scanner"
    rows = {v.title: v for v in db_session.query(Vulnerability).join(models.Host).filter(
        models.Host.project_id == test_project.id)}
    assert set(rows) == {"Outdated TLS library", "Default community string", "Host-level note",
                         "SMB signing not required"}
    assert all(v.source.value == "external" for v in rows.values())

    tls = rows["Outdated TLS library"]
    assert (tls.severity, tls.exploitable, tls.cvss_score, tls.cvss_version) == (
        VulnerabilitySeverity.HIGH, True, 9.8, "3.1")
    assert tls.cve_id == "CVE-2024-0001"
    assert sorted(c for (c,) in db_session.query(VulnerabilityCve.cve_id).filter(
        VulnerabilityCve.vulnerability_id == tls.id)) == ["CVE-2024-0001", "CVE-2024-0002"]
    assert tls.plugin_output == "openssl 1.0.1" and tls.plugin_id == "tls-1"
    assert tls.port.port_number == 443 and tls.port.state == "open" and tls.port.service_name == "https"
    assert tls.host.hostname == "web01.example.test"

    # Nothing stated: UNKNOWN severity, NULL exploit, no CVSS — never a default.
    snmp = rows["Default community string"]
    assert (snmp.severity, snmp.exploitable, snmp.cvss_score, snmp.cvss_version, snmp.cve_id) == (
        VulnerabilitySeverity.UNKNOWN, None, None, None, None)
    assert (snmp.port.port_number, snmp.port.protocol) == (161, "udp")
    # Stated none: false, host-level.
    note = rows["Host-level note"]
    assert (note.severity, note.exploitable, note.port_id) == (VulnerabilitySeverity.INFO, False, None)
    # A catalog check takes the catalog's title and severity; the line's evidence.
    smb = rows["SMB signing not required"]
    assert smb.check_id == "smb_signing_not_required" and smb.plugin_output and "signing: False" in smb.plugin_output

    # The five bad lines were skipped and counted, not guessed and not fatal.
    stats = parser.last_parse_stats
    assert stats["skipped"] == 5 and stats["partial"] is True
    for word in ("no host address", "severity 'scary'", "exploitable must be", "out of range", "not a catalog check"):
        assert word in stats["warnings"], stats["warnings"]
    assert not db_session.query(models.Host).filter(models.Host.ip_address == "10.92.0.3").first()


def test_the_tool_comes_from_the_line_then_the_upload_then_the_default(db_session, test_project, tmp_path):
    bare = [{"schema": "bluestick-observation/1", "host": "10.92.1.1", "title": "x"}]
    scan = ObservationsParser(db_session).parse_file(str(_write(tmp_path, bare, "a.jsonl")), "a.jsonl",
                                                     project_id=test_project.id, source_tool="my-script")
    assert scan.tool_name == "my-script"
    scan = ObservationsParser(db_session).parse_file(str(_write(tmp_path, bare, "b.jsonl")), "b.jsonl",
                                                     project_id=test_project.id)
    assert scan.tool_name == "external"


def test_a_file_with_no_observation_or_no_address_is_refused(db_session, test_project, tmp_path):
    with pytest.raises(ValueError, match="schema"):
        ObservationsParser(db_session).parse_file(
            str(_write(tmp_path, [{"template-id": "x"}], "n.jsonl")), "n.jsonl", project_id=test_project.id)
    with pytest.raises(ValueError, match="host address"):
        ObservationsParser(db_session).parse_file(
            str(_write(tmp_path, [LINES[4]], "h.jsonl")), "h.jsonl", project_id=test_project.id)
