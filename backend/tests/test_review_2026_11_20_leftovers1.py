"""Review 2026-11-20, what the seven batches left — first part.

* a run of NOTs in the host query language is capped as parentheses are;
* the ``<result>`` GMP nests inside a result's ``<detection>`` is not a
  result of the report (not checked against a real export: there is none);
* Nikto writes no port for a finding whose report names none; EyeWitness
  takes the address a URL is aimed at, not any dotted quad in it;
* a parser names the first few things it skipped and counts all of them;
* a streamed JSON document behind a byte-order mark is read;
* testssl written in parts ends as the file written whole;
* a number no port can have is refused by every writer and by the table;
* the changelog's people and "trimmed" are asked without a pass over every
  line; a keyless MCP batch is one telemetry row.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db import models
from app.db.models_project import ProjectEvent
from app.db.models_vulnerability import Vulnerability
from app.parsers import testssl_parser
from app.parsers.eyewitness_parser import EyewitnessParser
from app.parsers.nikto_parser import NiktoParser
from app.parsers.openvas_parser import OpenVASParser
from app.parsers.parser_utils import FirstFew
from app.parsers.streaming_json import iter_json_records
from app.parsers.testssl_parser import TestsslParser
from app.services import host_query_dsl, project_events
from app.services.host_deduplication_service import HostDeduplicationService, InvalidPort

NATIVE = Path(__file__).parent / "fixtures" / "native"


# --- the query language --------------------------------------------------------

def test_a_run_of_nots_is_capped_and_a_short_one_still_reads():
    node = host_query_dsl.parse_query("NOT NOT NOT port:22")
    depth = 0
    while isinstance(node, host_query_dsl.Not):
        node, depth = node.child, depth + 1
    assert depth == 3

    with pytest.raises(host_query_dsl.DSLError) as refused:
        host_query_dsl.parse_query("NOT " * 50 + "port:22")
    assert "NOT" in str(refused.value)


# --- OpenVAS -------------------------------------------------------------------

_NESTED = """<?xml version="1.0" encoding="UTF-8"?>
<report><results>
  <result id="r1">
    <name>Outdated product</name>
    <host>10.81.0.5</host>
    <port>443/tcp</port>
    <severity>7.5</severity>
    <threat>High</threat>
    <nvt oid="1.3.6.1.4.1.25623.1.0.1"><cve>N/A</cve></nvt>
    <detection><result id="d1"><details><detail><name>product</name><value>cpe:/a:x:y</value></detail></details></result></detection>
  </result>
</results></report>
"""


def test_a_detection_reference_is_not_a_result_of_the_report(db_session, test_project, tmp_path):
    path = tmp_path / "openvas.xml"
    path.write_text(_NESTED)
    parser = OpenVASParser(db_session)
    scan = parser.parse_file(str(path), "openvas.xml", project_id=test_project.id)
    db_session.flush()
    assert parser.last_parse_stats["skipped"] == 0 and parser.last_parse_stats["warnings"] is None
    assert db_session.query(Vulnerability).filter(Vulnerability.scan_id == scan.id).count() == 1


# --- Nikto, EyeWitness ---------------------------------------------------------

def test_nikto_writes_no_port_nobody_stated(db_session, test_project, tmp_path):
    path = tmp_path / "nikto.json"
    path.write_text(json.dumps([{"ip": "10.82.0.7", "id": "999100", "msg": "Something was observed."}]))
    scan = NiktoParser(db_session).parse_file(str(path), "nikto.json", project_id=test_project.id)
    db_session.flush()
    host = db_session.query(models.Host).filter_by(project_id=test_project.id, ip_address="10.82.0.7").one()
    assert db_session.query(models.Port).filter_by(host_id=host.id).count() == 0
    row = db_session.query(Vulnerability).filter(Vulnerability.scan_id == scan.id).one()
    assert row.port_id is None


@pytest.mark.parametrize("url, address", [
    ("http://10.83.0.9:8080/login", "10.83.0.9"),
    ("https://app.example.test/redirect?to=10.83.0.9", None),
    ("https://app.example.test/v/1.2.3.4/index", None),
    ("not a url", None),
])
def test_eyewitness_takes_the_address_a_url_is_aimed_at(url, address):
    assert EyewitnessParser._extract_ip_from_url(url) == address


def test_the_first_few_are_named_and_all_are_counted():
    few = FirstFew(shown=2)
    assert not few
    for n in range(5):
        few.add(f"line {n}")
    assert few and few.total == 5 and few.kept == ["line 0", "line 1"]
    assert few.listed() == "line 0; line 1 …"


# --- JSON ----------------------------------------------------------------------

def test_a_streamed_document_behind_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "big.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"hosts": [{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}]}).encode())
    records = list(iter_json_records(str(path), array_keys=("hosts",), tool_label="test", threshold_bytes=1))
    assert [r["ip"] for r in records] == ["10.0.0.1", "10.0.0.2"]


# --- testssl -------------------------------------------------------------------

def _tls_rows(db, scan):
    from app.db.models import WebInterface

    return sorted(
        (row.url, row.tls_weak_protocol, row.cert_self_signed)
        for row in db.query(WebInterface).filter(WebInterface.scan_id == scan.id)
    )


def test_testssl_written_in_parts_is_the_file_written_whole(db_session, test_project, tmp_path, monkeypatch):
    records = []
    for n in range(1, 6):
        ip = f"10.84.0.{n}"
        records += [
            {"id": "TLS1", "ip": f"host{n}.example.test/{ip}", "port": "443", "severity": "LOW",
             "finding": "offered (deprecated)"},
            {"id": "TLS1_2", "ip": f"host{n}.example.test/{ip}", "port": "443", "severity": "OK",
             "finding": "offered"},
        ]
    path = tmp_path / "testssl.json"
    path.write_text(json.dumps(records))

    whole_parser = TestsslParser(db_session)
    whole = whole_parser.parse_file(str(path), "testssl-whole.json", project_id=test_project.id)
    monkeypatch.setattr(testssl_parser, "_FLUSH_TARGETS", 2)
    parts_parser = TestsslParser(db_session)
    parts = parts_parser.parse_file(str(path), "testssl-parts.json", project_id=test_project.id)

    assert len(_tls_rows(db_session, whole)) == 5
    assert _tls_rows(db_session, parts) == _tls_rows(db_session, whole)
    assert parts_parser.last_parse_stats["summary"] == whole_parser.last_parse_stats["summary"]
    history = lambda scan: db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id).count()  # noqa: E731
    assert history(parts) == history(whole) == 5


# --- port numbers --------------------------------------------------------------

def test_no_writer_takes_a_number_no_port_can_have(db_session, test_project):
    scan = models.Scan(filename="p.txt", tool_name="test", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    dedup = HostDeduplicationService(db_session)
    host = dedup.find_or_create_host("10.85.0.1", scan.id, {}, project_id=test_project.id)
    with pytest.raises(InvalidPort):
        dedup.find_or_create_port(host.id, scan.id, {"port_number": 70000, "protocol": "tcp", "state": "open"})
    dedup.upsert_ports_bulk(scan.id, [
        (host.id, {"port_number": 0, "protocol": "tcp", "state": "open"}),
        (host.id, {"port_number": 443, "protocol": "tcp", "state": "open"}),
    ])
    db_session.flush()
    assert [p.port_number for p in db_session.query(models.Port).filter_by(host_id=host.id)] == [443]


def test_the_table_refuses_it_too(db_session, test_project):
    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("the constraint is the migration chain's")
    host = models.Host(ip_address="10.85.0.2", project_id=test_project.id)
    db_session.add(host)
    db_session.flush()
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.execute(text(
                "INSERT INTO ports_v2 (host_id, port_number, protocol) VALUES (:h, 70000, 'tcp')"
            ), {"h": host.id})
    assert "ck_ports_v2_port_number" in str(refused.value)


# --- the changelog -------------------------------------------------------------

def _line(db, project_id, user_id, action="host_review_status_changed"):
    db.add(ProjectEvent(
        project_id=project_id, actor_user_id=user_id, action=action,
        created_at=datetime.now(timezone.utc),
    ))


def test_the_people_are_those_with_a_line_here_and_trimmed_needs_no_second_count(
    db_session, test_project, test_user, monkeypatch,
):
    from app.db.models_auth import User

    other = User(username="nobody-here", email="nobody@example.test", hashed_password="x", role="member")
    db_session.add(other)
    db_session.flush()
    for _ in range(3):
        _line(db_session, test_project.id, test_user.id)
    db_session.flush()

    answer = project_events.list_events(db_session, test_project.id, limit=10, offset=0, include_remediation=True)
    assert [p["id"] for p in answer["people"]] == [test_user.id]
    assert answer["total"] == 3 and answer["trimmed"] is False

    monkeypatch.setattr(settings, "PROJECT_EVENTS_MAX_PER_PROJECT", 3)
    assert project_events.was_trimmed(db_session, test_project.id) is True          # asked by position
    assert project_events.was_trimmed(db_session, test_project.id, held=2) is False  # told the count
    filtered = project_events.list_events(
        db_session, test_project.id, limit=10, offset=0, include_remediation=True, actor_id=other.id,
    )
    assert filtered["total"] == 0 and filtered["trimmed"] is True
    monkeypatch.setattr(settings, "PROJECT_EVENTS_MAX_PER_PROJECT", 4)
    assert project_events.was_trimmed(db_session, test_project.id) is False


# --- MCP telemetry -------------------------------------------------------------

def test_a_batch_with_no_accepted_key_is_one_telemetry_row(monkeypatch):
    from app.api import deps
    from app.api.v1.endpoints import mcp_assist
    from app.services import mcp_telemetry_service as telemetry

    written: list = []
    monkeypatch.setattr(telemetry, "write_events", lambda events: written.append(list(events)))
    events = [
        telemetry.build_event(rpc_method="tools/call", tool_name="x", outcome=telemetry.REJECTED, duration_ms=2)
        for _ in range(50)
    ]

    monkeypatch.setattr(deps, "live_agent_session_id", lambda key: None)
    mcp_assist._write_batch_telemetry(events, None)
    (row,) = written.pop()
    assert row["rpc_method"] == "(batch)" and row["outcome"] == telemetry.REJECTED
    assert "50 messages" in row["detail"] and row["duration_ms"] == 100

    monkeypatch.setattr(deps, "live_agent_session_id", lambda key: 7)
    mcp_assist._write_batch_telemetry(events, "bsk_live")
    assert len(written.pop()) == 50

    monkeypatch.setattr(deps, "live_agent_session_id", lambda key: None)
    mcp_assist._write_batch_telemetry(events[:1], None)        # a single message is its own row
    assert written.pop()[0]["rpc_method"] == "tools/call"
