"""Tests for the dnsx review fixes (RV-1, RV-7).

RV-1: A/AAAA answers create host observations (so a resolve-only run isn't
      a host-less "empty" scan) and DNSRecord rows carry scan_id.
RV-7: SRV/CAA/ANY/AXFR record types are parsed; a >threshold JSONL file
      whose lines start with '{' streams instead of erroring.
"""
from __future__ import annotations

import json

from app.db import models
from app.parsers.dnsx_parser import DnsxParser
from app.parsers.streaming_json import iter_json_records


def _write_jsonl(tmp_path, name, records):
    path = tmp_path / name
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def test_a_record_creates_host_and_records_carry_scan_id(
    db_session, test_project, tmp_path,
):
    path = _write_jsonl(tmp_path, "a.jsonl", [
        {"host": "example.com", "a": ["93.184.216.34"],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
    ])
    scan = DnsxParser(db_session).parse_file(
        str(path), path.name, project_id=test_project.id,
    )

    # RV-1 — the resolved IP becomes a discovered host (hostname = domain).
    host = (
        db_session.query(models.Host)
        .filter(
            models.Host.project_id == test_project.id,
            models.Host.ip_address == "93.184.216.34",
        )
        .first()
    )
    assert host is not None
    assert host.hostname == "example.com"

    # RV-1 — DNS rows carry their scan_id for provenance / dns_record_count.
    rows = (
        db_session.query(models.DNSRecord)
        .filter(models.DNSRecord.project_id == test_project.id)
        .all()
    )
    assert rows and all(r.scan_id == scan.id for r in rows)


def test_a_record_does_not_overwrite_existing_hostname(db_session, test_project, tmp_path):
    """CR-A2/#4 — a forward A record must not clobber an existing
    (PTR/scanner) hostname; many vhosts share one IP."""
    # Pre-existing host with a canonical hostname (e.g. from a PTR/scan).
    existing = models.Host(
        project_id=test_project.id, ip_address="93.184.216.34",
        hostname="canonical.example.net", state="up",
    )
    db_session.add(existing)
    db_session.flush()

    path = _write_jsonl(tmp_path, "vhost.jsonl", [
        {"host": "some-other-vhost.example.com", "a": ["93.184.216.34"],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
    ])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    db_session.flush()

    db_session.refresh(existing)
    assert existing.hostname == "canonical.example.net"  # preserved, not overwritten


def test_a_record_fills_empty_hostname(db_session, test_project, tmp_path):
    """A/AAAA still populates an EMPTY hostname (and creates missing hosts)."""
    existing = models.Host(
        project_id=test_project.id, ip_address="93.184.216.35",
        hostname=None, state="unknown",
    )
    db_session.add(existing)
    db_session.flush()

    path = _write_jsonl(tmp_path, "fill.jsonl", [
        {"host": "fills-empty.example.com", "a": ["93.184.216.35"],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
    ])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    db_session.flush()
    db_session.refresh(existing)
    assert existing.hostname == "fills-empty.example.com"


def test_srv_and_caa_records_parsed(db_session, test_project, tmp_path):
    path = _write_jsonl(tmp_path, "srvcaa.jsonl", [
        {"host": "_sip._tcp.example.com", "srv": ["10 60 5060 sip.example.com"],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
        {"host": "example.com", "caa": ['0 issue "letsencrypt.org"'],
         "resolver": ["1.1.1.1:53"], "status_code": "NOERROR"},
    ])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    db_session.flush()  # parse_file adds rows; the caller commits — flush to query here

    types = {
        r.record_type
        for r in db_session.query(models.DNSRecord)
        .filter(models.DNSRecord.project_id == test_project.id)
        .all()
    }
    assert "SRV" in types
    assert "CAA" in types


def test_jsonl_streams_when_first_line_is_an_object(tmp_path):
    """RV-7 — a JSONL file (every line starts with '{') above the streaming
    threshold must stream line-by-line, not raise 'no array key found'."""
    path = _write_jsonl(tmp_path, "big.jsonl", [
        {"host": "a.example.com", "a": ["10.0.0.1"], "status_code": "NOERROR"},
        {"host": "b.example.com", "a": ["10.0.0.2"], "status_code": "NOERROR"},
        {"host": "c.example.com", "a": ["10.0.0.3"], "status_code": "NOERROR"},
    ])
    # threshold_bytes=1 forces the streaming path for this tiny file.
    records = list(iter_json_records(str(path), tool_label="dnsx JSON", threshold_bytes=1))
    assert len(records) == 3
    assert {r["host"] for r in records} == {"a.example.com", "b.example.com", "c.example.com"}
