"""Nessus findings land on the (port, PROTOCOL) they were reported on (v2.365.0).

Code review 2026-09-19, finding 4.  The parser captures ``protocol``; the ingest
path dropped it — it looked up and inserted ``'tcp'`` unconditionally and keyed
its per-host cache on the port number.  So every UDP finding (SNMP 161, NTP 123,
IKE 500, DNS 53/udp) created a phantom OPEN TCP port of the same number, or
attached itself to a real TCP service that happened to share it.  Pins:

* a UDP finding creates a UDP port, and no TCP port of that number;
* TCP and UDP findings on the SAME number are two endpoints, each with its own
  finding — through the informational-skip path too, which also upserts ports;
* a re-import does not duplicate either endpoint;
* an item with no protocol still defaults to TCP, as before.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from app.db import models
from app.db.models_vulnerability import Vulnerability
from app.services.nessus_integration_service import NessusIntegrationService

NESSUS_XML = textwrap.dedent("""\
    <?xml version="1.0" ?>
    <NessusClientData_v2>
    <Report name="udp">
    <ReportHost name="10.9.8.7">
      <HostProperties><tag name="host-ip">10.9.8.7</tag></HostProperties>
      <ReportItem port="161" svc_name="snmp" protocol="udp" severity="3" pluginID="41028" pluginName="SNMP Agent Default Community Name (public)">
        <description>Default community.</description><risk_factor>High</risk_factor>
      </ReportItem>
      <ReportItem port="53" svc_name="dns" protocol="udp" severity="2" pluginID="10539" pluginName="DNS Server Recursive Query Cache Poisoning Weakness">
        <description>Recursion.</description><risk_factor>Medium</risk_factor>
      </ReportItem>
      <ReportItem port="53" svc_name="dns" protocol="tcp" severity="2" pluginID="12217" pluginName="DNS Server Cache Snooping Remote Information Disclosure">
        <description>Snooping.</description><risk_factor>Medium</risk_factor>
      </ReportItem>
      <ReportItem port="123" svc_name="ntp" protocol="udp" severity="0" pluginID="10884" pluginName="Network Time Protocol (NTP) Server Detection">
        <description>NTP.</description>
      </ReportItem>
      <ReportItem port="8080" svc_name="www" severity="2" pluginID="99999" pluginName="Item with no protocol attribute">
        <description>Legacy shape.</description><risk_factor>Medium</risk_factor>
      </ReportItem>
    </ReportHost>
    </Report>
    </NessusClientData_v2>
""")


def _import(db, project_id, tmp_path: Path, **kw):
    path = tmp_path / "udp.nessus"
    path.write_text(NESSUS_XML)
    result = NessusIntegrationService(db).process_nessus_file(str(path), project_id=project_id, **kw)
    assert result["success"], result
    host = db.query(models.Host).filter(
        models.Host.project_id == project_id, models.Host.ip_address == "10.9.8.7").one()
    return host


def _endpoints(db, host):
    return sorted(
        (p.port_number, p.protocol)
        for p in db.query(models.Port).filter(models.Port.host_id == host.id).all()
    )


def test_udp_findings_create_udp_ports_not_phantom_tcp_ones(db_session, test_project, tmp_path):
    host = _import(db_session, test_project.id, tmp_path)
    assert _endpoints(db_session, host) == [
        (53, "tcp"), (53, "udp"), (123, "udp"), (161, "udp"), (8080, "tcp"),
    ]


def test_each_finding_is_on_the_endpoint_it_was_reported_on(db_session, test_project, tmp_path):
    host = _import(db_session, test_project.id, tmp_path)
    on = {
        v.plugin_id: (v.port.port_number, v.port.protocol)
        for v in db_session.query(Vulnerability).filter(Vulnerability.host_id == host.id).all()
        if v.port is not None
    }
    assert on["41028"] == (161, "udp")
    # The same NUMBER, two transports, two different findings — not merged.
    assert on["10539"] == (53, "udp")
    assert on["12217"] == (53, "tcp")
    assert on["99999"] == (8080, "tcp")   # no protocol attribute: the old default


def test_the_informational_skip_path_keeps_the_protocol_too(db_session, test_project, tmp_path):
    """Skipped severity-0 items still upsert their ports — through the same
    method, which had the same bug."""
    host = _import(db_session, test_project.id, tmp_path, skip_informational=True)
    assert (123, "udp") in _endpoints(db_session, host)
    assert (123, "tcp") not in _endpoints(db_session, host)


def test_ports_carry_the_nessus_service_name(db_session, test_project, tmp_path):
    """v2.416.0 — `svc_name` was parsed and dropped: a port only Nessus saw
    had no service at all."""
    host = _import(db_session, test_project.id, tmp_path, skip_informational=True)
    named = {
        (p.port_number, p.protocol): p.service_name
        for p in db_session.query(models.Port).filter(models.Port.host_id == host.id)
    }
    assert named[(161, "udp")] == "snmp"
    assert named[(8080, "tcp")] == "www"
    assert named[(123, "udp")] == "ntp"   # through the informational-skip path too


def test_the_nessus_service_name_never_replaces_another_tools(db_session, test_project, tmp_path):
    host = models.Host(project_id=test_project.id, ip_address="10.9.8.7", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.Port(host_id=host.id, port_number=8080, protocol="tcp", state="open",
                               service_name="http-proxy"))
    db_session.flush()
    host = _import(db_session, test_project.id, tmp_path)
    port = db_session.query(models.Port).filter(
        models.Port.host_id == host.id, models.Port.port_number == 8080).one()
    assert port.service_name == "http-proxy"


def test_a_reimport_does_not_duplicate_either_transport(db_session, test_project, tmp_path):
    pid = test_project.id   # the import detaches the fixture; read the id once
    host = _import(db_session, pid, tmp_path)
    before = _endpoints(db_session, host)
    host = _import(db_session, pid, tmp_path)
    assert _endpoints(db_session, host) == before
