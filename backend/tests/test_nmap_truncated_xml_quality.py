"""
A truncated Nmap XML must not report as a clean import (v2.232.0).

The parser recovers whatever hosts it read before the XML ran out, which is
the right behaviour — but it used to discard the fact that it had done so.
``parse_warnings`` was collected and then thrown away by a no-op
(``scan.command_line = existing_notes``) under a comment claiming to mark the
scan partial, and the parser never set ``last_parse_stats``, so the
orchestrator had nothing to persist. The operator saw a green "processed
successfully" with a host count.

That is the worst failure mode this tool can have: the hosts that were never
parsed are indistinguishable from hosts that were genuinely down, so partial
coverage reads as authoritative coverage.
"""

import pytest

from app.parsers.nmap_parser import NmapXMLParser


COMPLETE_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<nmaprun scanner="nmap" args="nmap -sV 10.0.0.0/24" start="1700000000" version="7.94">
<scaninfo type="syn" protocol="tcp" numservices="1000" services="1-1000"/>
<host starttime="1700000001" endtime="1700000002">
  <status state="up" reason="syn-ack"/>
  <address addr="10.77.0.1" addrtype="ipv4"/>
  <ports>
    <port protocol="tcp" portid="22">
      <state state="open" reason="syn-ack"/>
      <service name="ssh" product="OpenSSH" version="8.9"/>
    </port>
  </ports>
</host>
<host starttime="1700000003" endtime="1700000004">
  <status state="up" reason="syn-ack"/>
  <address addr="10.77.0.2" addrtype="ipv4"/>
  <ports>
    <port protocol="tcp" portid="443">
      <state state="open" reason="syn-ack"/>
      <service name="https"/>
    </port>
  </ports>
</host>
"""

TRUNCATED_XML = COMPLETE_HEAD + """<host starttime="1700000005">
  <status state="up" reason="syn-ack"/>
  <address addr="10.77.0.3" addrty"""

COMPLETE_XML = COMPLETE_HEAD + """<runstats><finished time="1700000010"/></runstats>
</nmaprun>
"""


def _parse(db_session, tmp_path, xml: str, name: str, project_id: int):
    path = tmp_path / name
    path.write_text(xml)
    parser = NmapXMLParser(db_session)
    scan = parser.parse_file(str(path), name, project_id=project_id)
    return parser, scan


def test_truncated_xml_recovers_hosts_and_reports_partial(
    db_session, tmp_path, test_project
):
    """The crux: hosts BEFORE the truncation are kept, and the job is told
    the import is incomplete."""
    parser, scan = _parse(
        db_session, tmp_path, TRUNCATED_XML, "truncated.xml", test_project.id
    )

    from app.db.models import Host

    recovered = (
        db_session.query(Host)
        .filter(Host.project_id == test_project.id, Host.ip_address.in_(["10.77.0.1", "10.77.0.2"]))
        .count()
    )
    assert recovered == 2, "hosts parsed before the truncation must be preserved"

    stats = getattr(parser, "last_parse_stats", None)
    assert stats, "parser must publish last_parse_stats so the job can persist it"
    assert stats["partial"] is True
    assert stats["warnings"], "a truncated file must produce a warning string"
    # The message has to say what it MEANS, not just that XML broke — the
    # operator's decision is 'do I trust this inventory?'.
    assert "MISSING" in stats["warnings"]
    assert "INCOMPLETE" in stats["summary"]


def test_complete_xml_reports_clean(db_session, tmp_path, test_project):
    """The signal is only useful if a good file stays quiet."""
    parser, scan = _parse(
        db_session, tmp_path, COMPLETE_XML, "complete.xml", test_project.id
    )

    stats = getattr(parser, "last_parse_stats", None)
    assert stats is not None
    assert stats["partial"] is False
    assert stats["warnings"] is None
    assert stats["skipped"] == 0
    assert "INCOMPLETE" not in stats["summary"]


def test_ingestion_job_schema_carries_quality_fields():
    """C3's fix is invisible without B2: the schema must actually return the
    columns, or the warning dies at the API boundary."""
    from app.schemas.schemas import IngestionJobSchema

    fields = IngestionJobSchema.model_fields
    assert "parser_warnings" in fields
    assert "skipped_count" in fields
