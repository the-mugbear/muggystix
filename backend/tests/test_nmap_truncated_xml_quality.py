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

from app.parsers.cut_short import TruncatedFile, begin_read, end_read
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


def _parse(db_session, tmp_path, xml: str, name: str, project_id: int, *, keep_truncated: bool = False):
    path = tmp_path / name
    path.write_text(xml)
    parser = NmapXMLParser(db_session)
    # As the dispatcher runs a parser: the read report open around it, with
    # the operator's "import what the file holds" when they gave it.
    reading = begin_read(keep_truncated=keep_truncated)
    try:
        scan = parser.parse_file(str(path), name, project_id=project_id)
    finally:
        end_read(reading)
    return parser, scan


def test_truncated_xml_fails_as_cut_short_until_asked_to_keep(db_session, tmp_path, test_project):
    """Owner, 2026-10-11: a file that was cut short is asked about, never kept
    silently.  Without the instruction the parser raises ``TruncatedFile``
    with the count of complete hosts (the dispatcher fails the import as
    ``truncated_file`` and removes what was written)."""
    with pytest.raises(TruncatedFile) as refused:
        _parse(db_session, tmp_path, TRUNCATED_XML, "truncated.xml", test_project.id)
    assert (refused.value.records_read, refused.value.unit) == (2, "host")
    assert str(refused.value) == (
        "This Nmap XML file was cut short: it ends after 2 complete hosts, before the end of the document."
    )


def test_a_cut_file_kept_as_asked_is_not_told_to_re_upload(db_session, tmp_path, test_project):
    """Review 2026-10-14 (nitpick): once the operator chose to import what
    the file holds, the warning still said "Re-upload a complete scan
    file"."""
    parser, _ = _parse(db_session, tmp_path, TRUNCATED_XML, "kept.xml", test_project.id, keep_truncated=True)
    # Nmap says only what it means; the count is the dispatcher's, once
    # (second review 2026-10-14 — it was said twice, with two counts).
    warnings = parser.last_parse_stats["warnings"]
    assert "Re-upload" not in warnings and "host(s)" not in warnings
    assert "MISSING from this import, not down" in warnings


# A COMPLETE nmap run (closed, with its <runstats>) of three hosts.  The second
# host's banner holds a control character, which nmap writes as a character
# reference — ``&#x1b;`` — and XML 1.0 does not allow: the reader stops there.
CONTROL_CHARACTER_XML = COMPLETE_HEAD.replace(
    '<service name="https"/>',
    '<service name="https"/><script id="banner" output="220 ready&#x1b;[0m"/>',
) + """<host starttime="1700000005" endtime="1700000006">
  <status state="up" reason="syn-ack"/>
  <address addr="10.77.0.3" addrtype="ipv4"/>
  <ports><port protocol="tcp" portid="80"><state state="open" reason="syn-ack"/></port></ports>
</host>
<runstats><finished time="1700000010"/></runstats>
</nmaprun>
"""

#: What this file imports as — at commit 86452af7 (before any cut-short work
#: touched this parser) and now.  Read off both by running this fixture
#: against ``git archive 86452af7 backend/app`` mounted over ``/app/app``.
CONTROL_CHARACTER_IMPORT = {
    "hosts": ["10.77.0.1"],
    "partial": True,
    "skipped": 0,
    "summary": "1 host (INCOMPLETE — file truncated)",
    "warning_starts": (
        "Incomplete XML — parsing stopped early, so hosts after this point are MISSING, not down. "
        "Recovered 1 host(s). Re-upload a complete scan file. ("
    ),
}


@pytest.mark.parametrize("keep", [False, True], ids=["as uploaded", "asked to keep"])
def test_invalid_xml_keeps_the_hosts_before_it_as_it_always_did(db_session, tmp_path, test_project, keep):
    """The owner's rule is about files that END ABRUPTLY, and nothing else
    (2026-10-11: existing behaviour for other files is unchanged).  This file
    does not end early — it holds a character XML does not allow — so it is
    not asked about and does not fail: the hosts read before that point are
    kept and the import is partial, exactly as before, whether or not the
    import was asked to keep a cut-short file."""
    assert "&#x1b;" in CONTROL_CHARACTER_XML and CONTROL_CHARACTER_XML.rstrip().endswith("</nmaprun>")
    parser, scan = _parse(
        db_session, tmp_path, CONTROL_CHARACTER_XML, "control.xml", test_project.id, keep_truncated=keep,
    )
    from app.db.models import Host

    hosts = sorted(
        ip for (ip,) in db_session.query(Host.ip_address).filter(Host.project_id == test_project.id)
    )
    stats = parser.last_parse_stats
    assert {
        "hosts": hosts, "partial": stats["partial"], "skipped": stats["skipped"],
        "summary": stats["summary"],
        "warning_starts": stats["warnings"][:len(CONTROL_CHARACTER_IMPORT["warning_starts"])],
    } == CONTROL_CHARACTER_IMPORT
    assert "xmlParseCharRef" in stats["warnings"]
    assert scan.end_time is None  # the reader never reached <finished>


def test_invalid_xml_is_not_offered_as_a_cut_short_file(db_session, tmp_path, test_project):
    """Through the job: completed and partial — no ``truncated_file``, nothing
    to choose."""
    from tests.ingestion_job_harness import host_ips, run_file

    path = tmp_path / "control.xml"
    path.write_text(CONTROL_CHARACTER_XML)
    job = run_file(db_session, test_project.id, path)
    assert (job.status, job.partial, job.parse_error_id) == ("completed", True, None)
    assert job.final_file_type == "nmap_xml"
    assert "MISSING, not down" in job.parser_warnings and "cut short" not in job.parser_warnings
    assert host_ips(db_session, test_project.id) == ["10.77.0.1"]


def test_truncated_xml_recovers_hosts_and_reports_partial(
    db_session, tmp_path, test_project
):
    """The crux: asked to keep what the file holds, hosts BEFORE the
    truncation are kept, and the job is told the import is incomplete."""
    parser, scan = _parse(
        db_session, tmp_path, TRUNCATED_XML, "truncated.xml", test_project.id, keep_truncated=True,
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
