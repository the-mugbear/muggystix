"""Loading a Host loads the host — nothing else (review 2026-09-23 B-Debt-1).

Host's ports, scanner rows, notes and tags were ``lazy="selectin"``, so every
whole-entity Host query also fetched all of them: the client-report build,
the out-of-scope export and the findings list loaded thousands of objects
nothing read (1,500 ports and 1,500 scanner rows for one findings page at 80k
hosts).  A path that reads them names the load; this pins the default.
"""
from sqlalchemy import event

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


def test_scanner_row_times_leave_the_api_with_a_utc_offset(client, db_session, test_project):
    """Review 2026-09-23 R8: stored without a zone, they were serialised with
    no offset and a browser read them as local time."""
    from datetime import datetime

    scan = models.Scan(project_id=test_project.id, filename="n.nessus")
    h = models.Host(project_id=test_project.id, ip_address="10.81.0.1", state="up")
    db_session.add_all([scan, h])
    db_session.flush()
    db_session.add(Vulnerability(host_id=h.id, scan_id=scan.id, title="t", severity=VulnerabilitySeverity.LOW,
                                 source=VulnerabilitySource.NESSUS,
                                 first_seen=datetime(2026, 9, 1, 12, 0, 0), last_seen=datetime(2026, 9, 2, 12, 0, 0)))
    db_session.commit()
    body = client.get(f"/api/v1/projects/{test_project.id}/hosts/{h.id}").json()
    (vuln,) = body["vulnerabilities"]
    assert vuln["first_seen"] in ("2026-09-01T12:00:00Z", "2026-09-01T12:00:00+00:00")
    assert vuln["last_seen"] in ("2026-09-02T12:00:00Z", "2026-09-02T12:00:00+00:00")


def test_querying_hosts_runs_one_statement(db_session, test_project):
    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="n.nessus")
    db_session.add(scan)
    db_session.flush()
    for i in range(3):
        h = models.Host(project_id=pid, ip_address=f"10.80.0.{i}", state="up")
        db_session.add(h)
        db_session.flush()
        db_session.add(models.Port(host_id=h.id, port_number=443, protocol="tcp", state="open"))
        db_session.add(Vulnerability(host_id=h.id, scan_id=scan.id, title="t", severity=VulnerabilitySeverity.LOW,
                                     source=VulnerabilitySource.NESSUS))
    db_session.commit()
    db_session.expunge_all()

    statements = []

    def count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):  # not the harness's SAVEPOINTs
            statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", count)
    try:
        hosts = db_session.query(models.Host).filter(models.Host.project_id == pid).all()
    finally:
        event.remove(bind, "before_cursor_execute", count)
    assert len(hosts) == 3
    assert len(statements) == 1, statements
