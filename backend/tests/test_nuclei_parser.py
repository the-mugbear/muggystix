"""Nuclei JSON results → scanner observations (v2.411.0).

Nuclei was advertised as ingestible (tool registry, output contract, AGENTS.md,
Tool Reference) with no parser; an upload was "not recognised".
``fixtures/native/nuclei-je.json`` is shaped like ``nuclei -je`` output (not a
live capture): two matchers of one template on a named host, a CVE template, a
network template, and a DNS template that carries no address.
"""
from __future__ import annotations

import json
import os

import pytest

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.parsers.content_detection import looks_like_nuclei
from app.parsers.nuclei_parser import NucleiParser

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "native", "nuclei-je.json")


def _parse(db_session, project, path=FIXTURE):
    parser = NucleiParser(db_session)
    scan = parser.parse_file(str(path), os.path.basename(str(path)), project_id=project.id)
    return parser, scan


def _vulns(db_session, scan):
    return {v.title: v for v in db_session.query(Vulnerability).filter(Vulnerability.scan_id == scan.id)}


class TestDetection:
    def test_array_export(self):
        assert looks_like_nuclei(open(FIXTURE, "rb").read(), "out.json")

    def test_jsonl(self):
        line = b'{"template-id":"x","matched-at":"10.0.0.1:22","ip":"10.0.0.1"}\n'
        assert looks_like_nuclei(line, "out.jsonl")

    def test_structure_not_filename(self):
        # The filename alone is not evidence (CLAUDE.md "Detection").
        assert not looks_like_nuclei(b'{"url":"https://x/","tech":["Nginx"],"status_code":200}', "nuclei.json")

    def test_rejects_httpx_and_naabu(self):
        assert not looks_like_nuclei(b'{"host":"x","port":443,"url":"https://x","ip":"1.2.3.4"}', "a.json")
        assert not looks_like_nuclei(b'{"ip":"1.2.3.4","port":22,"protocol":"tcp"}', "a.json")


def test_matches_become_observations_with_nuclei_severity(db_session, test_project):
    parser, scan = _parse(db_session, test_project)
    assert scan.tool_name == "nuclei"
    vulns = _vulns(db_session, scan)

    log4j = vulns["Apache Log4j2 Remote Code Injection"]
    assert log4j.source == VulnerabilitySource.NUCLEI
    assert log4j.severity == VulnerabilitySeverity.CRITICAL
    assert log4j.plugin_id == "CVE-2021-44228"
    assert log4j.cve_id == "CVE-2021-44228"
    assert log4j.cvss_score == 10
    assert log4j.solution.startswith("Upgrade to Log4j")
    assert "https://logging.apache.org/log4j/2.x/security.html" in (log4j.references or [])
    assert "Matched at: http://10.20.0.7:8080/api/login" in log4j.plugin_output
    assert log4j.port.port_number == 8080

    vnc = vulns["VNC Service - Detect"]
    assert vnc.severity == VulnerabilitySeverity.INFO
    assert vnc.port.port_number == 5900 and vnc.port.state == "open"
    assert "RFB 003.008" in vnc.plugin_output


def test_each_matcher_is_its_own_observation(db_session, test_project):
    """One template, two matchers (two missing headers) on one host: two rows,
    not one row that keeps the last matcher's name."""
    _, scan = _parse(db_session, test_project)
    titles = set(_vulns(db_session, scan))
    assert "HTTP Missing Security Headers: strict-transport-security" in titles
    assert "HTTP Missing Security Headers: content-security-policy" in titles


def test_named_target_binds_the_name_and_http_port(db_session, test_project):
    _, scan = _parse(db_session, test_project)
    hsts = _vulns(db_session, scan)["HTTP Missing Security Headers: strict-transport-security"]
    host = db_session.get(models.Host, hsts.host_id)
    assert host.ip_address == "10.20.0.5"
    assert hsts.name_id is not None
    assert hsts.port.port_number == 443 and hsts.port.service_name == "http"


def test_result_without_an_address_is_skipped_and_reported(db_session, test_project):
    parser, scan = _parse(db_session, test_project)
    assert "DNS WAF Detection" not in _vulns(db_session, scan)
    assert parser.last_parse_stats["skipped"] == 1
    assert parser.last_parse_stats["partial"] is True
    assert "no IP address" in parser.last_parse_stats["warnings"]


def test_scan_window_from_result_timestamps(db_session, test_project):
    _, scan = _parse(db_session, test_project)
    assert scan.start_time is not None and scan.end_time is not None
    assert scan.end_time > scan.start_time


def test_reimport_in_one_file_does_not_duplicate(db_session, test_project, tmp_path):
    records = json.load(open(FIXTURE))
    path = tmp_path / "twice.json"
    path.write_text(json.dumps(records + records))
    _, scan = _parse(db_session, test_project, path)
    rows = db_session.query(Vulnerability).filter(Vulnerability.scan_id == scan.id).count()
    assert rows == 4  # two header matchers + log4j + vnc


def test_empty_and_address_less_files_fail_closed(db_session, test_project, tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    with pytest.raises(ValueError, match="no results"):
        _parse(db_session, test_project, empty)

    dns_only = tmp_path / "dns.json"
    dns_only.write_text(json.dumps([{"template-id": "dns-waf-detect", "info": {"name": "x", "severity": "info"},
                                     "type": "dns", "host": "example.com", "matched-at": "example.com"}]))
    with pytest.raises(ValueError, match="no IP address"):
        _parse(db_session, test_project, dns_only)
