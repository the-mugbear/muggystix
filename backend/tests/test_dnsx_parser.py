"""Contract tests for the dnsx JSON / JSONL parser (v2.88.0, closes #44).

Covers:
- Content-based detection (looks_like_dnsx) — host + record-type fields
- JSONL ingestion — one record per line, multiple record types per host
- PTR feeds Host.hostname (mirrors DNSParser CSV behaviour)
- Multiple-resolver dedup — identical answers from different resolvers
  produce one DNSRecord row, not duplicates
- Resolution failures (NXDOMAIN/SERVFAIL) counted but don't yield rows
- Fail-closed on zero-record file

Parser-level logic against the real Postgres test DB.  Does NOT
round-trip through HTTP upload for ingest correctness; the live
ingest path is the thin dispatcher wrapper covered by the dispatcher
unit tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import models


# ---------------------------------------------------------------------------
# Detector tests (pure functions, no fixtures needed)
# ---------------------------------------------------------------------------


class TestLooksLikeDnsx:
    def test_filename_match(self):
        from app.parsers.content_detection import looks_like_dnsx
        assert looks_like_dnsx(b"", "dnsx-output.json")

    def test_content_match_a_record(self):
        from app.parsers.content_detection import looks_like_dnsx
        sample = b'{"host":"example.com","a":["93.184.216.34"],"resolver":["1.1.1.1:53"]}'
        assert looks_like_dnsx(sample, "out.jsonl")

    def test_content_match_ptr(self):
        from app.parsers.content_detection import looks_like_dnsx
        sample = b'{"host":"10.0.0.5","ptr":["mail.example.com"],"resolver":["8.8.8.8:53"]}'
        assert looks_like_dnsx(sample, "out.json")

    def test_rejects_amass_shape(self):
        """Amass uses `name` + `addresses`; dnsx uses `host` + DNS record
        fields.  Detector must not false-positive on amass output."""
        from app.parsers.content_detection import looks_like_dnsx
        sample = b'{"name":"example.com","addresses":[{"ip":"1.2.3.4"}]}'
        assert not looks_like_dnsx(sample, "amass.json")

    def test_rejects_httpx_shape(self):
        from app.parsers.content_detection import looks_like_dnsx
        sample = b'{"url":"https://example.com/","tech":["Nginx"],"status_code":200}'
        assert not looks_like_dnsx(sample, "httpx.jsonl")

    def test_rejects_plain_object(self):
        from app.parsers.content_detection import looks_like_dnsx
        assert not looks_like_dnsx(b'{"foo":"bar"}', "random.json")


# ---------------------------------------------------------------------------
# Parser-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dnsx_fixture_jsonl(tmp_path):
    """Multi-record dnsx JSONL covering A / AAAA / PTR / CNAME / MX,
    a NXDOMAIN failure, and two resolvers giving the same A answer
    (dedup case)."""
    records = [
        # Forward lookups against resolver 1.1.1.1
        {"host": "example.com", "a": ["93.184.216.34"],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR", "ttl": 86400},
        {"host": "example.com", "aaaa": ["2606:2800:220:1:248:1893:25c8:1946"],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
        {"host": "www.example.com", "cname": ["example.com"],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
        {"host": "example.com", "mx": ["10 mail.example.com"],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
        # Same A record from a second resolver — should dedup.
        {"host": "example.com", "a": ["93.184.216.34"],
         "resolver": ["8.8.8.8:53"], "status_code": "NOERROR"},
        # PTR lookup — should populate Host.hostname inventory.
        {"host": "10.0.0.5", "ptr": ["mail.internal"],
         "resolver": ["8.8.8.8:53"], "status_code": "NOERROR"},
        # NXDOMAIN — counted in warnings but produces no DNSRecord row.
        {"host": "does-not-exist.example", "resolver": ["1.1.1.1:53"],
         "status_code": "NXDOMAIN"},
    ]
    path = tmp_path / "dnsx-out.jsonl"
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestDnsxParser:
    def test_writes_dns_records(self, db_session, test_project, dnsx_fixture_jsonl):
        """A 7-record fixture (one NXDOMAIN) produces 6 DNSRecord rows
        under v2.89.0 (#44.1) semantics — the same A record from two
        different resolvers is now two rows (one per resolver) instead
        of one collapsed row.  Pre-#44.1 the duplicate dedup'd."""
        from app.parsers.dnsx_parser import DnsxParser

        parser = DnsxParser(db_session)
        scan = parser.parse_file(
            str(dnsx_fixture_jsonl),
            dnsx_fixture_jsonl.name,
            project_id=test_project.id,
        )
        assert scan.tool_name == "dnsx"
        assert scan.scan_type == "dns_resolution"
        assert scan.project_id == test_project.id

        rows = (
            db_session.query(models.DNSRecord)
            .filter(models.DNSRecord.project_id == test_project.id)
            .all()
        )
        by_type = {r.record_type for r in rows}
        assert by_type == {"A", "AAAA", "CNAME", "MX", "PTR"}
        # 6 rows: 2x A (one per resolver), 1x AAAA, 1x CNAME, 1x MX,
        # 1x PTR.  NXDOMAIN produces nothing.
        assert len(rows) == 6

        # PTR is keyed by hostname (matching DNSParser CSV behaviour:
        # the PTR record points the hostname back at the IP).
        ptr = next(r for r in rows if r.record_type == "PTR")
        assert ptr.domain == "mail.internal"
        assert ptr.value == "10.0.0.5"
        assert ptr.resolver_name == "8.8.8.8:53"

        # TTL captured when present (1.1.1.1's response carried ttl=86400;
        # 8.8.8.8's duplicate omitted ttl and persists as NULL — both
        # behaviours are correct, just per-row).
        a_records = [r for r in rows if r.record_type == "A"]
        ttls_by_resolver = {r.resolver_name: r.ttl for r in a_records}
        assert ttls_by_resolver["1.1.1.1:53"] == 86400
        assert ttls_by_resolver["8.8.8.8:53"] is None

        # The same A record from two resolvers → two rows, each
        # tagged with its own resolver_name.  Closes #44.1.
        a_resolvers = sorted(r.resolver_name for r in a_records)
        assert a_resolvers == ["1.1.1.1:53", "8.8.8.8:53"]

    def test_resolver_split_query_pattern(
        self, db_session, test_project, dnsx_fixture_jsonl,
    ):
        """The whole point of #44.1: a filter by resolver_name
        returns just that resolver's view of the data.  Smoke-tests
        the "what did 1.1.1.1 see that 8.8.8.8 didn't" analytical
        pattern."""
        from app.parsers.dnsx_parser import DnsxParser

        parser = DnsxParser(db_session)
        parser.parse_file(
            str(dnsx_fixture_jsonl),
            dnsx_fixture_jsonl.name,
            project_id=test_project.id,
        )
        # 1.1.1.1 answered: A, AAAA, CNAME, MX (4 records).
        one_one = (
            db_session.query(models.DNSRecord)
            .filter(
                models.DNSRecord.project_id == test_project.id,
                models.DNSRecord.resolver_name == "1.1.1.1:53",
            )
            .all()
        )
        assert len(one_one) == 4
        assert {r.record_type for r in one_one} == {"A", "AAAA", "CNAME", "MX"}

        # 8.8.8.8 answered: A (the duplicate) + PTR (2 records).
        eight_eight = (
            db_session.query(models.DNSRecord)
            .filter(
                models.DNSRecord.project_id == test_project.id,
                models.DNSRecord.resolver_name == "8.8.8.8:53",
            )
            .all()
        )
        assert len(eight_eight) == 2
        assert {r.record_type for r in eight_eight} == {"A", "PTR"}

    def test_ptr_populates_host_hostname(
        self, db_session, test_project, dnsx_fixture_jsonl,
    ):
        """A successful reverse lookup should create/update the host
        with the discovered hostname — same special-case the CSV DNS
        parser uses."""
        from app.parsers.dnsx_parser import DnsxParser

        parser = DnsxParser(db_session)
        parser.parse_file(
            str(dnsx_fixture_jsonl),
            dnsx_fixture_jsonl.name,
            project_id=test_project.id,
        )
        host = (
            db_session.query(models.Host)
            .filter(
                models.Host.project_id == test_project.id,
                models.Host.ip_address == "10.0.0.5",
            )
            .first()
        )
        assert host is not None
        assert host.hostname == "mail.internal"

    def test_parser_stats_capture_resolvers_and_failures(
        self, db_session, test_project, dnsx_fixture_jsonl,
    ):
        """``last_parse_stats.warnings`` should mention every resolver
        with its hit count and the NXDOMAIN failure tally."""
        from app.parsers.dnsx_parser import DnsxParser

        parser = DnsxParser(db_session)
        parser.parse_file(
            str(dnsx_fixture_jsonl),
            dnsx_fixture_jsonl.name,
            project_id=test_project.id,
        )
        warnings = parser.last_parse_stats.get("warnings") or ""
        assert "1.1.1.1:53" in warnings
        assert "8.8.8.8:53" in warnings
        assert "NXDOMAIN" in warnings

    def test_fail_closed_on_zero_records(self, db_session, test_project, tmp_path):
        """A file with no parseable answers (e.g. only NXDOMAIN) should
        raise ValueError instead of silently committing an empty scan
        — same fail-closed shape as the CSV DNSParser."""
        from app.parsers.dnsx_parser import DnsxParser

        path = tmp_path / "empty.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps({
                "host": "nothing.example",
                "resolver": ["1.1.1.1:53"],
                "status_code": "NXDOMAIN",
            }) + "\n")

        parser = DnsxParser(db_session)
        with pytest.raises(ValueError, match="0 valid DNS records"):
            parser.parse_file(str(path), path.name, project_id=test_project.id)
