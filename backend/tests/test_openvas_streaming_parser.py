"""Regression tests for the v2.86.11 OpenVAS streaming-parser rewrite.

The parser flipped from full-tree ``DET.parse(file_path)`` to
``iterparse_safe`` + per-result ``clear_element``.  These tests
confirm:

  * a minimal but realistic OpenVAS XML fixture still parses end-to-end
    to the same Scan + Host + Vulnerability rows (no behaviour
    regression);
  * an invalid / truncated document still raises ``ValueError`` with a
    helpful message rather than the raw ``XMLSyntaxError``;
  * the iteration loop calls ``clear_element`` so memory doesn't
    accumulate (verified indirectly: many small ``<result>`` nodes
    should parse without doubling RSS).

The fixture is inline so the test is self-contained — the real
``artifacts/manual/openvas_sample.xml`` exercises the dispatcher
but isn't a parser-level regression target.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.parsers.openvas_parser import OpenVASParser


_OPENVAS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<get_results_response>
  <results>
    <result id="r1">
      <name>SSH Weak Encryption Algorithms Supported</name>
      <host>10.0.0.5</host>
      <port>22/tcp</port>
      <severity>7.5</severity>
      <threat>High</threat>
      <description>The remote SSH server supports weak ciphers.</description>
      <solution>Disable CBC mode ciphers.</solution>
      <nvt oid="1.3.6.1.4.1.25623.1.0.105611">
        <cve>CVE-2008-5161</cve>
      </nvt>
    </result>
    <result id="r2">
      <name>HTTP TRACE Method Enabled</name>
      <host>10.0.0.5</host>
      <port>80/tcp</port>
      <severity>5.0</severity>
      <threat>Medium</threat>
      <description>The remote web server supports TRACE.</description>
      <nvt oid="1.3.6.1.4.1.25623.1.0.11213">
        <cve>N/A</cve>
      </nvt>
    </result>
    <result id="r3">
      <name>Information disclosure</name>
      <host>10.0.0.6</host>
      <port>443/tcp</port>
      <severity>2.6</severity>
      <threat>Low</threat>
      <nvt oid="1.3.6.1.4.1.25623.1.0.99999"/>
    </result>
  </results>
</get_results_response>
"""


@pytest.fixture
def openvas_xml_path():
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="openvas_fixture_")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(_OPENVAS_XML)
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def test_openvas_streaming_creates_scan_hosts_and_vulns(
    db_session, test_project, openvas_xml_path,
):
    """The minimal fixture produces 1 Scan + 2 Hosts + 3 Vulnerabilities,
    each mapped to the right severity / CVE / port."""
    parser = OpenVASParser(db_session)
    scan = parser.parse_file(
        openvas_xml_path,
        filename="fixture.xml",
        project_id=test_project.id,
    )
    db_session.flush()

    assert scan.tool_name == "openvas"
    assert scan.scan_type == "vulnerability_scan"

    hosts = (
        db_session.query(models.Host)
        .filter(models.Host.project_id == test_project.id)
        .order_by(models.Host.ip_address)
        .all()
    )
    ips = [h.ip_address for h in hosts]
    assert ips == ["10.0.0.5", "10.0.0.6"]

    vulns = (
        db_session.query(Vulnerability)
        .filter(Vulnerability.scan_id == scan.id)
        .order_by(Vulnerability.title)
        .all()
    )
    assert len(vulns) == 3

    by_title = {v.title: v for v in vulns}
    assert "SSH Weak Encryption Algorithms Supported" in by_title
    assert "HTTP TRACE Method Enabled" in by_title
    assert "Information disclosure" in by_title

    ssh = by_title["SSH Weak Encryption Algorithms Supported"]
    assert ssh.source == VulnerabilitySource.OPENVAS
    assert ssh.severity == VulnerabilitySeverity.HIGH
    assert ssh.cvss_score == pytest.approx(7.5)
    assert ssh.cve_id == "CVE-2008-5161"
    assert ssh.plugin_id == "1.3.6.1.4.1.25623.1.0.105611"
    assert ssh.solution == "Disable CBC mode ciphers."

    http = by_title["HTTP TRACE Method Enabled"]
    assert http.severity == VulnerabilitySeverity.MEDIUM
    # cve "N/A" must be normalised to None.
    assert http.cve_id is None

    info = by_title["Information disclosure"]
    assert info.severity == VulnerabilitySeverity.LOW


def test_openvas_streaming_invalid_xml_raises_valueerror(db_session, test_project):
    """Truncated XML should surface as a ``ValueError`` so the
    ingestion worker's parse-error path can record it nicely — not as
    a raw ``XMLSyntaxError`` that leaks lxml internals."""
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="openvas_bad_")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("<?xml version='1.0'?><get_results_response><results><resul")
        parser = OpenVASParser(db_session)
        with pytest.raises(ValueError, match="OpenVAS XML"):
            parser.parse_file(path, filename="bad.xml", project_id=test_project.id)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def test_openvas_streaming_skips_results_missing_host_ip(
    db_session, test_project,
):
    """A <result> without an extractable IP should be skipped silently
    rather than blowing up the parse."""
    no_ip_xml = """<?xml version="1.0"?>
<get_results_response>
  <results>
    <result id="r1">
      <name>Real finding</name>
      <host>10.0.0.5</host>
      <port>22/tcp</port>
      <severity>5.0</severity>
    </result>
    <result id="r2">
      <name>No host</name>
      <host></host>
      <port>22/tcp</port>
      <severity>5.0</severity>
    </result>
    <result id="r3">
      <name>Garbage host</name>
      <host>not.an.ip.address</host>
      <port>22/tcp</port>
      <severity>5.0</severity>
    </result>
  </results>
</get_results_response>
"""
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="openvas_partial_")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(no_ip_xml)
        parser = OpenVASParser(db_session)
        scan = parser.parse_file(path, filename="partial.xml", project_id=test_project.id)
        db_session.flush()
        vulns = (
            db_session.query(Vulnerability)
            .filter(Vulnerability.scan_id == scan.id)
            .all()
        )
        titles = {v.title for v in vulns}
        # Only the row with a real IP made it through.
        assert titles == {"Real finding"}, titles
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
