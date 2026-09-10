"""v2.333.0 — where a scan's time comes from, and what each scan contributed.

Two defects this pins:

* Scan times were naive and unlabelled.  ``ensure_scan`` stamped the upload
  time into ``start_time`` for tools whose output carries no time, masscan
  used the container's local zone, nmap/masscan fell back to ``utcnow()`` for
  a missing end, and the API emitted ``start_time`` without an offset next to
  a tz-aware ``created_at`` — so the browser showed one converted and the
  other not.  Every parser now records ``time_source``; the API tags absolute
  times as UTC and leaves a zone-less scanner clock naive.
* /scans could only say "N new hosts".  It now reports, per scan, what the
  scan's own rows contain: new ports and named services (port_scan_history),
  web interfaces, name observations, netexec results, and the spread of the
  findings it recorded first.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.parsers.parser_utils import ScanClock, epoch_to_utc, parse_rfc3339
from app.schemas.schemas import ScanSummary
from app.services.host_deduplication_service import HostDeduplicationService
from tests.conftest import USING_POSTGRES

pg_only = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="MasscanParser's bulk path is PostgreSQL-specific SQL; runs only against the Postgres test DB.",
)


def _epoch(seconds: int) -> datetime:
    return datetime(1970, 1, 1) + timedelta(seconds=seconds)


def _write(tmp_path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content)
    return str(path)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


class TestTimeHelpers:
    def test_epoch_is_utc_not_container_local(self):
        assert epoch_to_utc("1640995200") == datetime(2022, 1, 1)
        assert epoch_to_utc(1640995200.5) == datetime(2022, 1, 1, 0, 0, 0, 500000)
        assert epoch_to_utc(None) is None
        assert epoch_to_utc("") is None
        assert epoch_to_utc("not-a-number") is None
        assert epoch_to_utc(0) is None

    def test_rfc3339_accepts_go_nanoseconds_and_rejects_a_zoneless_value(self):
        assert parse_rfc3339("2026-09-08T10:11:12.123456789Z") == datetime(
            2026, 9, 8, 10, 11, 12, 123456, tzinfo=timezone.utc
        )
        assert parse_rfc3339("2026-09-08T06:11:12-04:00").astimezone(timezone.utc).hour == 10
        # No offset: the zone is unknown, so no guess.
        assert parse_rfc3339("2026-09-08T10:11:12") is None
        assert parse_rfc3339(None) is None

    def test_clock_prefers_absolute_times_and_keeps_an_explicit_run_window(self):
        clock = ScanClock()
        clock.observe_clock(datetime(2026, 1, 1, 9))
        clock.observe(datetime(2026, 1, 1, 15, tzinfo=timezone.utc))
        clock.observe(datetime(2026, 1, 1, 10, tzinfo=timezone(timedelta(hours=-4))))  # 14:00Z
        scan = models.Scan(filename="x")
        clock.apply(scan)
        assert (scan.start_time, scan.end_time, scan.time_source) == (
            datetime(2026, 1, 1, 14),
            datetime(2026, 1, 1, 15),
            models.SCAN_TIME_TOOL_RECORDS,
        )
        explicit = models.Scan(filename="y", start_time=datetime(2020, 1, 1), time_source="tool_run")
        clock.apply(explicit)
        assert explicit.start_time == datetime(2020, 1, 1)
        assert explicit.time_source == "tool_run"

    def test_clock_falls_back_to_the_wall_clock_and_says_so(self):
        clock = ScanClock()
        clock.observe_clock(datetime(2026, 1, 1, 9))
        scan = models.Scan(filename="z")
        clock.apply(scan)
        assert scan.start_time == datetime(2026, 1, 1, 9)
        assert scan.end_time is None
        assert scan.time_source == models.SCAN_TIME_TOOL_CLOCK


class TestApiSerialisation:
    """The JSON must say what a time is: UTC offset for absolute times, none
    for a scanner wall clock (so the browser shows it as written)."""

    @staticmethod
    def _json(**kwargs) -> dict:
        summary = ScanSummary(
            id=1, filename="f", created_at=datetime(2026, 9, 10, 3, tzinfo=timezone.utc),
            total_hosts=0, up_hosts=0, total_ports=0, open_ports=0, **kwargs,
        )
        return json.loads(summary.model_dump_json())

    def test_absolute_times_carry_a_utc_offset(self):
        body = self._json(
            start_time=datetime(2026, 9, 10, 2, 51, 7),
            end_time=datetime(2026, 9, 10, 3, 1, 7),
            time_source="tool_run",
        )
        assert body["start_time"] == "2026-09-10T02:51:07Z"
        assert body["end_time"] == "2026-09-10T03:01:07Z"
        assert body["created_at"] == "2026-09-10T03:00:00Z"

    def test_a_scanner_wall_clock_stays_naive(self):
        body = self._json(start_time=datetime(2024, 7, 15, 10, 30, 1), time_source="tool_clock")
        assert body["start_time"] == "2024-07-15T10:30:01"

    def test_legacy_rows_keep_the_utc_convention(self):
        body = self._json(start_time=datetime(2026, 9, 10, 2, 0), time_source=None)
        assert body["start_time"] == "2026-09-10T02:00:00Z"


# ---------------------------------------------------------------------------
# Parsers record where their time came from
# ---------------------------------------------------------------------------


class TestParserTimeSources:
    def test_nmap_xml_run_times_are_utc(self, db_session, test_project, tmp_path, sample_nmap_xml):
        from app.parsers.nmap_parser import NmapXMLParser

        scan = NmapXMLParser(db_session).parse_file(
            _write(tmp_path, "n.xml", sample_nmap_xml), "n.xml", project_id=test_project.id
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_RUN
        assert scan.start_time == datetime(2022, 1, 1)  # start="1640995200"

    def test_nmap_without_finished_leaves_the_end_unknown(self, db_session, test_project, tmp_path):
        from app.parsers.nmap_parser import NmapXMLParser

        xml = (
            '<?xml version="1.0"?><nmaprun scanner="nmap" start="1640995200" version="7.94">'
            '<host><status state="up"/><address addr="10.9.0.9" addrtype="ipv4"/></host>'
        )  # truncated: no <runstats><finished>
        scan = NmapXMLParser(db_session).parse_file(
            _write(tmp_path, "t.xml", xml), "t.xml", project_id=test_project.id
        )
        # Used to be utcnow() — the upload time presented as the finish time.
        assert scan.end_time is None

    def test_gnmap_is_the_scanner_wall_clock(self, db_session, test_project, tmp_path, sample_gnmap_data):
        from app.parsers.gnmap_parser import GnmapParser

        scan = GnmapParser(db_session).parse_file(
            _write(tmp_path, "g.gnmap", sample_gnmap_data), "g.gnmap", project_id=test_project.id
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_CLOCK
        assert scan.start_time == datetime(2024, 7, 15, 10, 30, 1)

    @pg_only
    def test_masscan_list_window_is_the_record_span(self, db_session, test_project, tmp_path):
        from app.parsers.masscan_parser import MasscanParser

        content = "#masscan\nopen tcp 80 10.9.0.1 1711938600\nopen tcp 443 10.9.0.2 1711938660\n# end\n"
        scan = MasscanParser(db_session).parse_file(
            _write(tmp_path, "m.txt", content), "m.txt", project_id=test_project.id
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_RECORDS
        assert (scan.start_time, scan.end_time) == (_epoch(1711938600), _epoch(1711938660))

    @pg_only
    def test_masscan_json_window_is_the_record_span(self, db_session, test_project, tmp_path):
        from app.parsers.masscan_parser import MasscanParser

        records = [
            {"ip": "10.9.0.3", "timestamp": "1711936800", "ports": [{"port": 53, "proto": "tcp", "status": "open"}]},
            {"ip": "10.9.0.4", "timestamp": "1711936900", "ports": [{"port": 22, "proto": "tcp", "status": "open"}]},
        ]
        scan = MasscanParser(db_session).parse_file(
            _write(tmp_path, "m.json", json.dumps(records)), "m.json", project_id=test_project.id
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_RECORDS
        assert (scan.start_time, scan.end_time) == (_epoch(1711936800), _epoch(1711936900))

    def test_naabu_json_window_is_the_record_span(self, db_session, test_project, tmp_path):
        from app.parsers.naabu_parser import NaabuParser

        lines = [
            {"ip": "10.9.1.1", "port": 80, "timestamp": "2026-09-01T10:00:00.123456789Z"},
            {"ip": "10.9.1.2", "port": 443, "timestamp": "2026-09-01T10:05:00Z"},
        ]
        scan = NaabuParser(db_session).parse_file(
            _write(tmp_path, "n.jsonl", "\n".join(json.dumps(x) for x in lines)),
            "n.jsonl", project_id=test_project.id,
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_RECORDS
        assert scan.start_time == datetime(2026, 9, 1, 10, 0, 0, 123456)
        assert scan.end_time == datetime(2026, 9, 1, 10, 5)

    def test_output_without_a_time_is_not_given_the_upload_time(self, db_session, test_project, tmp_path):
        from app.parsers.naabu_parser import NaabuParser

        scan = NaabuParser(db_session).parse_file(
            _write(tmp_path, "n.txt", "10.9.1.3:22\n"), "n.txt", project_id=test_project.id
        )
        assert scan.start_time is None
        assert scan.time_source is None
        assert scan.created_at is not None

    def test_httpx_window_is_the_probe_span(self, db_session, test_project, tmp_path):
        from app.parsers.httpx_parser import HttpxParser

        records = [
            {"timestamp": "2026-04-16T04:00:00Z", "url": "https://10.9.2.10/", "host": "10.9.2.10",
             "port": "443", "scheme": "https", "status_code": 200},
            {"timestamp": "2026-04-16T00:00:01-04:00", "url": "http://10.9.2.20:8080/", "host": "10.9.2.20",
             "port": "8080", "scheme": "http", "status_code": 302},
        ]
        scan = HttpxParser(db_session).parse_file(
            _write(tmp_path, "h.jsonl", "\n".join(json.dumps(r) for r in records)),
            "h.jsonl", project_id=test_project.id,
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_RECORDS
        assert (scan.start_time, scan.end_time) == (datetime(2026, 4, 16, 4), datetime(2026, 4, 16, 4, 0, 1))

    def test_openvas_report_records_its_run_window(self, db_session, test_project, tmp_path):
        from app.parsers.openvas_parser import OpenVASParser

        xml = """<?xml version="1.0"?>
<report id="a"><report id="b">
  <scan_start>2026-09-01T02:00:00Z</scan_start>
  <results>
    <result id="r1"><name>SSH weak ciphers</name><host>10.9.3.1</host><port>22/tcp</port>
      <severity>5.0</severity><threat>Medium</threat><description>d</description>
      <nvt oid="1.3.6.1.4.1.25623.1.0.1"><cve>N/A</cve></nvt></result>
  </results>
  <scan_end>2026-09-01T03:30:00+01:00</scan_end>
</report></report>"""
        scan = OpenVASParser(db_session).parse_file(
            _write(tmp_path, "o.xml", xml), "o.xml", project_id=test_project.id
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_RUN
        assert (scan.start_time, scan.end_time) == (datetime(2026, 9, 1, 2), datetime(2026, 9, 1, 2, 30))

    _NIKTO = """- Nikto v2.5.0
---------------------------------------------------------------------------
+ Target IP:          10.9.4.1
+ Target Hostname:    10.9.4.1
+ Target Port:        80
+ Start Time:         2026-09-01 10:00:00{zone}
---------------------------------------------------------------------------
+ /: The anti-clickjacking X-Frame-Options header is not present.
+ End Time:           2026-09-01 10:02:30{zone} (150 seconds)
---------------------------------------------------------------------------
+ 1 host(s) tested
"""

    def test_nikto_start_and_end_are_run_times_not_findings(self, db_session, test_project, tmp_path):
        from app.parsers.nikto_parser import NiktoParser

        scan = NiktoParser(db_session).parse_file(
            _write(tmp_path, "k.txt", self._NIKTO.format(zone=" (GMT-4)")), "k.txt",
            project_id=test_project.id,
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_RUN
        assert (scan.start_time, scan.end_time) == (datetime(2026, 9, 1, 14), datetime(2026, 9, 1, 14, 2, 30))
        titles = [v.title for v in db_session.query(Vulnerability).filter(Vulnerability.scan_id == scan.id)]
        assert titles == ["/: The anti-clickjacking X-Frame-Options header is not present."]

    def test_nikto_without_an_offset_is_the_scanner_wall_clock(self, db_session, test_project, tmp_path):
        from app.parsers.nikto_parser import NiktoParser

        scan = NiktoParser(db_session).parse_file(
            _write(tmp_path, "k.txt", self._NIKTO.format(zone="")), "k.txt", project_id=test_project.id
        )
        assert scan.time_source == models.SCAN_TIME_TOOL_CLOCK
        assert scan.start_time == datetime(2026, 9, 1, 10)

    def test_nessus_window_spans_its_host_scan_times(self, db_session, test_project, tmp_path):
        from app.services.nessus_integration_service import NessusIntegrationService

        xml = """<?xml version="1.0" ?>
<NessusClientData_v2>
<Report name="Weekly" xmlns:cm="http://www.nessus.org/cm">
<ReportHost name="10.9.5.1"><HostProperties>
  <tag name="HOST_END_TIMESTAMP">1712241952</tag>
  <tag name="HOST_START_TIMESTAMP">1712241052</tag>
  <tag name="host-ip">10.9.5.1</tag>
</HostProperties>
<ReportItem port="22" svc_name="ssh" protocol="tcp" severity="2" pluginID="10001" pluginName="Test plugin" pluginFamily="General">
  <risk_factor>Medium</risk_factor><description>d</description><solution>s</solution><synopsis>syn</synopsis>
</ReportItem>
</ReportHost>
<ReportHost name="10.9.5.2"><HostProperties>
  <tag name="HOST_START_TIMESTAMP">1712240000</tag>
  <tag name="HOST_END_TIMESTAMP">1712240500</tag>
  <tag name="host-ip">10.9.5.2</tag>
</HostProperties></ReportHost>
</Report>
</NessusClientData_v2>"""
        result = NessusIntegrationService(db_session).process_nessus_file(
            _write(tmp_path, "w.nessus", xml), project_id=test_project.id
        )
        scan = db_session.get(models.Scan, result["scan_id"])
        assert scan.time_source == models.SCAN_TIME_TOOL_RECORDS
        assert (scan.start_time, scan.end_time) == (_epoch(1712240000), _epoch(1712241952))


# ---------------------------------------------------------------------------
# port_scan_history.port_created / service_name
# ---------------------------------------------------------------------------


class TestPortContribution:
    def test_dedup_marks_the_scan_that_introduced_a_port(self, db_session, test_project):
        pid = test_project.id
        first = models.Scan(project_id=pid, filename="a.xml", tool_name="nmap", scan_type="nmap")
        second = models.Scan(project_id=pid, filename="b.xml", tool_name="nmap", scan_type="nmap")
        db_session.add_all([first, second])
        db_session.flush()

        svc = HostDeduplicationService(db_session)
        host = svc.find_or_create_host("10.9.6.1", first.id, {"state": "up"}, project_id=pid)
        svc.find_or_create_port(host.id, first.id, {"port_number": 22, "protocol": "tcp", "state": "open", "service_name": "ssh"})
        svc = HostDeduplicationService(db_session)
        host = svc.find_or_create_host("10.9.6.1", second.id, {"state": "up"}, project_id=pid)
        svc.find_or_create_port(host.id, second.id, {"port_number": 22, "protocol": "tcp", "state": "open"})
        svc.find_or_create_port(host.id, second.id, {"port_number": 80, "protocol": "tcp", "state": "open", "service_name": "http"})
        db_session.commit()

        rows = {(r.scan_id, r.port.port_number): r for r in db_session.query(models.PortScanHistory)}
        assert (rows[(first.id, 22)].port_created, rows[(first.id, 22)].service_name) == (True, "ssh")
        assert (rows[(second.id, 22)].port_created, rows[(second.id, 22)].service_name) == (False, None)
        assert (rows[(second.id, 80)].port_created, rows[(second.id, 80)].service_name) == (True, "http")

    @pg_only
    def test_masscan_bulk_path_marks_new_ports(self, db_session, test_project, tmp_path):
        from app.parsers.masscan_parser import MasscanParser

        pid = test_project.id
        first = MasscanParser(db_session).parse_file(
            _write(tmp_path, "1.txt", "open tcp 80 10.9.6.2 1711938600\n"), "1.txt", project_id=pid
        )
        second = MasscanParser(db_session).parse_file(
            _write(tmp_path, "2.txt", "open tcp 80 10.9.6.2 1711938700\nopen tcp 443 10.9.6.2 1711938700\n"),
            "2.txt", project_id=pid,
        )
        created = {
            (r.scan_id, r.port.port_number): r.port_created
            for r in db_session.query(models.PortScanHistory).filter(
                models.PortScanHistory.scan_id.in_([first.id, second.id])
            )
        }
        assert created == {(first.id, 80): True, (second.id, 80): False, (second.id, 443): True}


# ---------------------------------------------------------------------------
# GET /scans — per-scan contribution blocks + time provenance + sort
# ---------------------------------------------------------------------------


class TestScansListContribution:
    def test_blocks_come_from_the_rows_each_scan_wrote(self, client, db_session, test_project):
        pid = test_project.id
        earlier = models.Scan(project_id=pid, filename="old.jsonl", tool_name="httpx", scan_type="web_fingerprint")
        scan = models.Scan(
            project_id=pid, filename="new.jsonl", tool_name="httpx", scan_type="web_fingerprint",
            start_time=datetime(2026, 9, 1, 10), end_time=datetime(2026, 9, 1, 10, 5),
            time_source=models.SCAN_TIME_TOOL_RECORDS,
        )
        db_session.add_all([earlier, scan])
        db_session.flush()
        host = models.Host(ip_address="10.9.7.1", project_id=pid, state="up")
        db_session.add(host)
        db_session.flush()

        db_session.add(models.HostScanHistory(
            host_id=host.id, scan_id=scan.id, state_at_scan="up", host_created=True, os_info_updated=True,
        ))
        port = models.Port(host_id=host.id, port_number=443, protocol="tcp", state="open")
        db_session.add(port)
        db_session.flush()
        db_session.add(models.PortScanHistory(
            port_id=port.id, scan_id=scan.id, state_at_scan="open", port_created=True, service_name="https",
        ))

        # Web: one URL an earlier scan already recorded, one new.
        db_session.add_all([
            models.WebInterface(scan_id=earlier.id, project_id=pid, host_id=host.id, source="httpx",
                                url="https://10.9.7.1/", protocol="https", status_code=200),
            models.WebInterface(scan_id=scan.id, project_id=pid, host_id=host.id, source="httpx",
                                url="https://10.9.7.1/", protocol="https", status_code=200,
                                cert_not_after=datetime(2000, 1, 1, tzinfo=timezone.utc),
                                cert_self_signed=True),
            models.WebInterface(scan_id=scan.id, project_id=pid, host_id=host.id, source="httpx",
                                url="http://10.9.7.1:8080/", protocol="http", status_code=404,
                                screenshot_path="shot.png"),
        ])

        # Names: one already observed by the earlier scan, one new (unresolved).
        known = models.DNSName(project_id=pid, fqdn="a.example.com")
        fresh = models.DNSName(project_id=pid, fqdn="b.example.com")
        db_session.add_all([known, fresh])
        db_session.flush()
        db_session.add_all([
            models.DNSRecord(project_id=pid, name_id=known.id, scan_id=earlier.id,
                             domain="a.example.com", record_type="A", value="10.9.7.1"),
            models.DNSRecord(project_id=pid, name_id=known.id, scan_id=scan.id,
                             domain="a.example.com", record_type="A", value="10.9.7.1"),
            models.DNSRecord(project_id=pid, name_id=fresh.id, scan_id=scan.id,
                             domain="b.example.com", record_type="DISCOVERED", value="httpx"),
        ])

        db_session.add_all([
            NetexecResult(scan_id=scan.id, host_id=host.id, protocol="smb", auth_success=True, username="svc"),
            NetexecResult(scan_id=scan.id, host_id=host.id, protocol="LDAP", auth_success=False, username="x"),
        ])
        db_session.add(Vulnerability(
            title="Critical thing", severity=VulnerabilitySeverity.CRITICAL,
            source=VulnerabilitySource.NESSUS, host_id=host.id, scan_id=scan.id, exploitable=True,
        ))
        db_session.commit()

        rows = {r["id"]: r for r in client.get(f"/api/v1/projects/{pid}/scans/").json()}
        row = rows[scan.id]
        assert row["time_source"] == "tool_records"
        assert row["start_time"] == "2026-09-01T10:00:00Z"
        assert row["end_time"] == "2026-09-01T10:05:00Z"
        assert row["os_fingerprinted"] == 1
        assert (row["port_breakdown"]["new_open_ports"], row["port_breakdown"]["open_with_service"]) == (1, 1)

        web = row["web"]
        assert (web["interfaces"], web["new_urls"], web["hosts"], web["https"]) == (2, 1, 1, 1)
        assert (web["status_2xx"], web["status_4xx"]) == (1, 1)
        assert (web["cert_expired"], web["cert_self_signed"], web["screenshots"]) == (1, 1, 1)

        dns = row["dns"]
        assert (dns["records"], dns["names"], dns["new_names"]) == (2, 2, 1)
        assert dns["by_type"] == {"A": 1, "DISCOVERED": 1}

        assert row["auth"] == {"hosts": 1, "protocols": ["ldap", "smb"], "valid_accounts": 1}

        vulns = row["vulnerability_summary"]
        assert (vulns["hosts_affected"], vulns["hosts_critical_high"], vulns["exploitable"]) == (1, 1, 1)

        old = rows[earlier.id]
        assert old["start_time"] is None and old["time_source"] is None
        assert old["web"]["new_urls"] == 1
        assert old["dns"]["new_names"] == 1
        assert old["auth"] is None
        assert old["port_breakdown"] is None

    def test_sort_by_start_time_puts_scans_without_a_time_last(self, client, db_session, test_project):
        pid = test_project.id
        none = models.Scan(project_id=pid, filename="none.txt", tool_name="whatweb")
        january = models.Scan(project_id=pid, filename="jan.xml", tool_name="nmap",
                              start_time=datetime(2026, 1, 1), time_source="tool_run")
        june = models.Scan(project_id=pid, filename="jun.xml", tool_name="nmap",
                           start_time=datetime(2026, 6, 1), time_source="tool_run")
        db_session.add_all([none, january, june])
        db_session.commit()

        def order(direction: str):
            body = client.get(f"/api/v1/projects/{pid}/scans/?sort_by=start_time&sort_order={direction}").json()
            return [r["id"] for r in body]

        assert order("desc") == [june.id, january.id, none.id]
        assert order("asc") == [january.id, june.id, none.id]


# ---------------------------------------------------------------------------
# v2.333.1 — review follow-up
#
# Invariants: an unusable OPTIONAL time never costs the observations it came
# with; a zone-less time is never presented as an absolute one; an account
# count counts accounts.
# ---------------------------------------------------------------------------


class TestUnusableTimesNeverAbortAnImport:
    @pytest.mark.parametrize("raw", ["NaN", "nan", "inf", "-inf", "1e400", float("nan"), float("inf")])
    def test_nonfinite_epochs_are_unknown(self, raw):
        assert epoch_to_utc(raw) is None

    @pytest.mark.parametrize("raw", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
    def test_instants_that_cannot_be_expressed_in_utc_are_rejected(self, raw):
        assert parse_rfc3339(raw) is None

    def test_the_clock_never_raises(self):
        clock = ScanClock()
        clock.observe(datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))))  # overflows as UTC
        clock.observe_clock("not a datetime")  # type: ignore[arg-type]
        scan = models.Scan(filename="x")
        clock.apply(scan)
        assert scan.start_time is None and scan.time_source is None

    @pg_only
    def test_masscan_nan_timestamp_keeps_the_record(self, db_session, test_project, tmp_path):
        from app.parsers.masscan_parser import MasscanParser

        pid = test_project.id
        records = [
            {"ip": "10.9.9.1", "timestamp": "NaN", "ports": [{"port": 80, "proto": "tcp", "status": "open"}]},
            {"ip": "10.9.9.2", "timestamp": "1711936800", "ports": [{"port": 22, "proto": "tcp", "status": "open"}]},
        ]
        scan = MasscanParser(db_session).parse_file(
            _write(tmp_path, "nan.json", json.dumps(records)), "nan.json", project_id=pid
        )
        observed = {
            h.host.ip_address
            for h in db_session.query(models.HostScanHistory).filter(models.HostScanHistory.scan_id == scan.id)
        }
        assert observed == {"10.9.9.1", "10.9.9.2"}
        assert db_session.query(models.PortScanHistory).filter(models.PortScanHistory.scan_id == scan.id).count() == 2
        # The good record still dates the scan; the NaN one contributes nothing.
        assert (scan.start_time, scan.end_time) == (_epoch(1711936800), None)

        only_nan = MasscanParser(db_session).parse_file(
            _write(tmp_path, "nan2.json", json.dumps(records[:1])), "nan2.json", project_id=pid
        )
        assert only_nan.start_time is None and only_nan.time_source is None

    def test_nmap_nan_epochs_keep_the_scan(self, db_session, test_project, tmp_path):
        from app.parsers.nmap_parser import NmapXMLParser

        xml = (
            '<?xml version="1.0"?><nmaprun scanner="nmap" start="NaN" version="7.94">'
            '<host><status state="up"/><address addr="10.9.9.4" addrtype="ipv4"/>'
            '<ports><port protocol="tcp" portid="22"><state state="open"/></port></ports></host>'
            '<runstats><finished time="NaN"/></runstats></nmaprun>'
        )
        scan = NmapXMLParser(db_session).parse_file(
            _write(tmp_path, "nan.xml", xml), "nan.xml", project_id=test_project.id
        )
        assert (scan.start_time, scan.end_time, scan.time_source) == (None, None, None)
        assert db_session.query(models.HostScanHistory).filter(models.HostScanHistory.scan_id == scan.id).count() == 1

    def test_httpx_out_of_range_timestamp_keeps_the_interface(self, db_session, test_project, tmp_path):
        from app.parsers.httpx_parser import HttpxParser

        record = {"timestamp": "0001-01-01T00:00:00+01:00", "url": "https://10.9.9.5/", "host": "10.9.9.5",
                  "port": "443", "scheme": "https", "status_code": 200}
        scan = HttpxParser(db_session).parse_file(
            _write(tmp_path, "far.jsonl", json.dumps(record)), "far.jsonl", project_id=test_project.id
        )
        assert db_session.query(models.WebInterface).filter(models.WebInterface.scan_id == scan.id).count() == 1
        assert scan.start_time is None

    def test_nikto_impossible_offset_keeps_the_findings(self, db_session, test_project, tmp_path):
        from app.parsers.nikto_parser import NiktoParser

        text = TestParserTimeSources._NIKTO.format(zone=" (GMT+99)")
        scan = NiktoParser(db_session).parse_file(
            _write(tmp_path, "k.txt", text), "k.txt", project_id=test_project.id
        )
        assert db_session.query(Vulnerability).filter(Vulnerability.scan_id == scan.id).count() == 1
        assert scan.start_time is None


class TestDnsxTimestamps:
    @staticmethod
    def _parse(db_session, test_project, tmp_path, rows):
        from app.parsers.dnsx_parser import DnsxParser

        return DnsxParser(db_session).parse_file(
            _write(tmp_path, "d.jsonl", "\n".join(json.dumps(r) for r in rows)),
            "d.jsonl", project_id=test_project.id,
        )

    def test_a_zoneless_timestamp_is_not_presented_as_an_absolute_window(self, db_session, test_project, tmp_path):
        scan = self._parse(db_session, test_project, tmp_path, [
            {"host": "a.zoneless.example", "a": ["10.9.10.1"], "status_code": "NOERROR",
             "timestamp": "2026-09-01T10:00:00"},
        ])
        assert scan.start_time is None and scan.time_source is None
        record = (
            db_session.query(models.DNSRecord)
            .filter(models.DNSRecord.scan_id == scan.id, models.DNSRecord.record_type == "A")
            .one()
        )
        # Not the invented instant: the observation takes the ingest time.
        assert record.observed_at != datetime(2026, 9, 1, 10, tzinfo=timezone.utc)

    def test_offset_timestamps_set_the_window_and_zoneless_ones_are_left_out(self, db_session, test_project, tmp_path):
        scan = self._parse(db_session, test_project, tmp_path, [
            {"host": "b.example", "a": ["10.9.10.2"], "status_code": "NOERROR",
             "timestamp": "2026-09-01T06:00:00-04:00"},
            {"host": "c.example", "a": ["10.9.10.3"], "status_code": "NOERROR",
             "timestamp": "2026-09-01T10:30:00.123456789Z"},
            {"host": "d.example", "a": ["10.9.10.4"], "status_code": "NOERROR",
             "timestamp": "2026-09-01T23:00:00"},
        ])
        assert scan.time_source == models.SCAN_TIME_TOOL_RECORDS
        assert (scan.start_time, scan.end_time) == (
            datetime(2026, 9, 1, 10), datetime(2026, 9, 1, 10, 30, 0, 123456)
        )


class TestAuthAccountIdentity:
    def test_accounts_are_domain_qualified_and_case_insensitive(self, client, db_session, test_project):
        pid = test_project.id
        scan = models.Scan(project_id=pid, filename="nxc.txt", tool_name="netexec", scan_type="netexec")
        db_session.add(scan)
        db_session.flush()
        h1 = models.Host(ip_address="10.9.11.1", project_id=pid, state="up")
        h2 = models.Host(ip_address="10.9.11.2", project_id=pid, state="up")
        db_session.add_all([h1, h2])
        db_session.flush()
        results = [
            # (host, protocol, port, domain, username, success)
            (h1, "smb", 445, "CORP", "svc", True),     # CORP\svc
            (h2, "smb", 445, "corp", "SVC", True),     # the same account on another host
            (h1, "ldap", 389, "LAB", "svc", True),     # a different account, same username
            (h1, "winrm", 5985, None, "admin", True),  # no domain: local to h1
            (h2, "winrm", 5985, None, "admin", True),  # no domain: local to h2
            (h2, "rdp", 3389, "CORP", "guest", False),  # failed: not an account
            (h1, "mssql", 1433, "CORP", "", True),     # null session: not an account
        ]
        for host, protocol, port, domain, username, success in results:
            db_session.add(NetexecResult(
                scan_id=scan.id, host_id=host.id, protocol=protocol, port=port,
                domain_name=domain, username=username, auth_success=success,
            ))
        db_session.commit()

        row = {r["id"]: r for r in client.get(f"/api/v1/projects/{pid}/scans/").json()}[scan.id]
        # CORP\svc, LAB\svc, admin@h1, admin@h2.
        assert row["auth"]["valid_accounts"] == 4
        assert row["auth"]["hosts"] == 2
