"""A failed Nessus import RAISES into the dispatcher's one failure path
(review 2026-10-23, H-ING2).

``NessusIntegrationService.process_nessus_file`` caught every exception and
answered ``{'success': False, …}``, and the dispatcher turned that answer into
a terminal ``ParseFailure``.  So:

(a) any unreadable ``.xml`` ended "Failed to process Nessus file: …" as
    ``nessus_xml`` (Nessus is the XML chain's last try), not the dispatcher's
    own failure for the file;
(b) an nmap ``.xml`` whose text trips the Nessus sniff (Nessus is tried first)
    never reached nmap;
(c) a database error's ``[SQL: …]`` text became the job's message.
"""
from __future__ import annotations

import textwrap

from sqlalchemy.exc import OperationalError

from app.db import models
from app.parsers import parser_utils
from tests.ingestion_job_harness import host_ips, job_row, queue_file, run_next, scans_of

NMAP_THAT_LOOKS_LIKE_NESSUS = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <nmaprun scanner="nmap" args="nmap -sV 10.20.30.40" start="1700000000" version="7.94" xmloutputversion="1.05">
    <!-- notes: tenable pluginid reportitem — words an analyst pasted into the comment -->
    <host starttime="1700000000" endtime="1700000100">
      <status state="up" reason="syn-ack"/>
      <address addr="10.20.30.40" addrtype="ipv4"/>
      <ports>
        <port protocol="tcp" portid="22"><state state="open" reason="syn-ack"/><service name="ssh"/></port>
      </ports>
    </host>
    <runstats><finished time="1700000100" elapsed="100"/><hosts up="1" down="0" total="1"/></runstats>
    </nmaprun>
""")

NESSUS = textwrap.dedent("""\
    <?xml version="1.0" ?>
    <NessusClientData_v2>
    <Report name="r">
    <ReportHost name="10.9.8.7">
      <HostProperties><tag name="host-ip">10.9.8.7</tag></HostProperties>
      <ReportItem port="22" svc_name="ssh" protocol="tcp" severity="2" pluginID="10267" pluginName="SSH Server Type and Version Information">
        <description>SSH.</description><risk_factor>Medium</risk_factor>
      </ReportItem>
    </ReportHost>
    </Report>
    </NessusClientData_v2>
""")


def _parse_error(db, job):
    return db.get(models.ParseError, job.parse_error_id)


def test_an_unrelated_xml_fails_as_the_dispatchers_failure_not_as_nessus(db_session, test_project, tmp_path):
    path = tmp_path / "inventory.xml"
    path.write_text('<?xml version="1.0"?><inventory><item name="x"/></inventory>')
    job_id = queue_file(db_session, test_project.id, path)
    run_next(db_session)

    job = job_row(db_session, job_id)
    assert job.status == "failed"
    # The dispatcher's sentence (the last parser's own diagnosis, as for any
    # chain), not the Nessus service's wrapper of it.
    assert "Failed to process Nessus file" not in (job.error_message or ""), job.error_message
    assert "Nothing from this file was kept" not in (job.error_message or "")
    error = _parse_error(db_session, job)
    assert error.file_type != "nessus_xml"
    # The dispatcher's own failure: the first format it tried for the file.
    assert error.file_type == "nmap_xml"
    assert scans_of(db_session, test_project.id) == []


def test_an_nmap_xml_that_trips_the_nessus_sniff_is_imported_by_nmap(db_session, test_project, tmp_path):
    from app.parsers.content_detection import is_nessus_sample

    assert is_nessus_sample(NMAP_THAT_LOOKS_LIKE_NESSUS.encode())  # Nessus is tried first
    path = tmp_path / "scan.xml"
    path.write_text(NMAP_THAT_LOOKS_LIKE_NESSUS)
    job_id = queue_file(db_session, test_project.id, path)
    run_next(db_session)

    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert job.final_file_type == "nmap_xml"
    assert host_ips(db_session, test_project.id) == ["10.20.30.40"]
    assert len(scans_of(db_session, test_project.id)) == 1


def test_a_database_error_does_not_put_sql_in_the_jobs_message(db_session, test_project, tmp_path, monkeypatch):
    def refuse(self, scan):
        raise OperationalError("UPDATE scans SET start_time=%(t)s WHERE scans.id = %(id)s", {}, Exception("server closed the connection"))

    # The last step before the import's final commit.
    monkeypatch.setattr(parser_utils.ScanClock, "apply", refuse)
    path = tmp_path / "export.nessus"
    path.write_text(NESSUS)
    job_id = queue_file(db_session, test_project.id, path)
    run_next(db_session)

    job = job_row(db_session, job_id)
    assert job.status == "failed"
    assert "[SQL:" not in (job.error_message or ""), job.error_message
    assert "[SQL:" not in (job.message or ""), job.message
    # Nothing of the attempt is kept.
    assert scans_of(db_session, test_project.id) == []
    assert host_ips(db_session, test_project.id) == []
