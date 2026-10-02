"""The report's "how it was confirmed" cap is applied by the database
(review 2026-10-02 R2).

``ClientReportService._confirmations`` loaded EVERY evidence record with
outcome ``finding`` of the report's findings — command, summary and output
preview included — sorted them in Python and kept ten per finding.  It now
ranks and cuts in SQL.  What it returns must not have changed: ``_reference``
below is the old selection, written out, and the service is compared with it
on a project built to hit every branch (more than the cap, tied times, no
``executed_at``, a false-positive endpoint, a host the finding is not on, a
finding with no systems, person and agent records).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_findings import Finding, FindingHost
from app.db.models_proposals import EvidenceRecord
from app.services import client_report_service as crs
from app.services.client_report_service import ClientReportService

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
CAP = crs.CONFIRMATIONS_PER_FINDING


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    return host


def _finding(db, project, title, hosts=()):
    f = Finding(project_id=project.id, title=title, severity="high", status="confirmed", source="manual")
    db.add(f)
    db.flush()
    for h in hosts:
        host, status = h if isinstance(h, tuple) else (h, "open")
        db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status=status))
    db.flush()
    return f


def _record(db, project, finding, host, *, run=None, recorded=None, outcome="finding", **kw):
    """``run`` / ``recorded``: minutes after T0 for ``executed_at`` /
    ``created_at`` (``run=None`` leaves ``executed_at`` NULL)."""
    rec = EvidenceRecord(
        project_id=project.id, host_id=host.id, finding_id=finding.id, tool="nmap",
        command=f"nmap {host.ip_address}", outcome=outcome, summary="Vulnerable.",
        raw_output_preview="VULNERABLE", raw_output_bytes=10,
        executed_at=T0 + timedelta(minutes=run) if run is not None else None,
        created_at=T0 + timedelta(minutes=recorded if recorded is not None else 10_000),
        **kw,
    )
    db.add(rec)
    db.flush()
    return rec


def _reference(db, findings):
    """The selection as it was made before R2: every record, sorted here,
    endpoint eligibility and the cap applied row by row."""
    by_id = {f.id: f for f in findings}
    rows = (
        db.query(EvidenceRecord)
        .filter(EvidenceRecord.finding_id.in_(list(by_id)), EvidenceRecord.outcome == "finding")
        .all()
    )
    rows.sort(key=lambda r: (r.executed_at or r.created_at, r.id))
    shown, omitted, by_agent = {}, {}, 0
    for r in rows:
        finding = by_id[r.finding_id]
        if finding.hosts and r.host_id not in {fh.host_id for fh in finding._report_endpoints}:
            continue
        if len(shown.setdefault(finding.id, [])) >= CAP:
            omitted[finding.id] = omitted.get(finding.id, 0) + 1
            continue
        shown[finding.id].append(r.id)
        by_agent += bool(r.agent_session_id)
    return shown, omitted, by_agent


class _EvidenceReads:
    """The rows each SELECT over ``evidence_records`` returned."""

    def __enter__(self):
        self.rowcounts = []
        event.listen(Engine, "after_cursor_execute", self._record)
        return self

    def _record(self, _conn, cursor, statement, _params, _context, _many):
        if "FROM evidence_records" in " ".join(statement.split()):
            self.rowcounts.append(cursor.rowcount)

    def __exit__(self, *exc):
        event.remove(Engine, "after_cursor_execute", self._record)


def test_the_database_cut_selects_what_the_python_cut_selected(db_session, test_project, test_user):
    db, project = db_session, test_project
    a, b, c, d, e = (_host(db, project, f"10.90.0.{n}") for n in range(1, 6))
    session = AgentSession(workflow="project", project_id=project.id, started_by_id=test_user.id)
    db.add(session)
    db.flush()
    agent, person = {"agent_session_id": session.id}, {"recorded_by_user_id": test_user.id}

    # More than the cap on listed systems (a open, c remediated), with a
    # false-positive endpoint (b) and a host the finding is not on (d).
    busy = _finding(db, project, "Busy", hosts=[a, (b, "false_positive"), (c, "remediated")])
    _record(db, project, busy, b, run=0, **person)                    # earliest of all, on the FP endpoint
    _record(db, project, busy, d, run=1, **agent)                     # not an endpoint
    for n in range(4):                                                # four at the SAME minute: id decides
        _record(db, project, busy, a if n % 2 else c, run=30, **(agent if n % 2 else person))
    _record(db, project, busy, a, run=None, recorded=5, **agent)      # no executed_at: recorded early
    _record(db, project, busy, a, run=None, recorded=30)              # ties with the four above, later id
    _record(db, project, busy, a, run=None, recorded=20_000, **person)   # recorded last of all
    for n in range(8):                                                # run in REVERSE order of id
        _record(db, project, busy, a, run=200 - n, **(person if n % 3 else agent))
    _record(db, project, busy, a, run=2, outcome="no_finding")        # never a confirmation
    _record(db, project, busy, b, run=40, **agent)
    _record(db, project, busy, c, run=3, **agent)

    # No systems at all: every record, whatever its host; over the cap.
    loose = _finding(db, project, "Loose")
    for n in range(CAP + 2):
        _record(db, project, loose, (a, d, e)[n % 3], run=500 - n if n % 4 else None,
                recorded=400 + n, **(agent if n % 2 else {}))

    few = _finding(db, project, "Few", hosts=[e])                     # under the cap
    for n in range(3):
        _record(db, project, few, e, run=10, **person)
    elsewhere = _finding(db, project, "Elsewhere", hosts=[e])         # records only on other hosts
    _record(db, project, elsewhere, a, run=1)
    _record(db, project, elsewhere, d, run=2)
    quiet = _finding(db, project, "Quiet", hosts=[a])                 # none at all
    dropped = _finding(db, project, "All false positives", hosts=[(a, "false_positive")])
    _record(db, project, dropped, a, run=1)                           # the finding is not in the report
    db.commit()
    db.expire_all()

    service = ClientReportService(db)
    findings = service._included(project.id)
    assert dropped.id not in {f.id for f in findings}
    expected_shown, expected_omitted, expected_by_agent = _reference(db, findings)
    # The fixture does hit the branches it was built for.
    assert len(expected_shown[busy.id]) == CAP and expected_omitted[busy.id] == 6
    assert len(expected_shown[loose.id]) == CAP and expected_omitted[loose.id] == 2
    assert len(expected_shown[few.id]) == 3 and few.id not in expected_omitted
    assert elsewhere.id not in expected_shown and quiet.id not in expected_shown
    assert sum(len(v) for v in expected_shown.values()) + sum(expected_omitted.values()) == 31

    with _EvidenceReads() as reads:
        shown, omitted, by_agent = service._confirmations(findings)

    assert {fid: [entry["id"] for entry in entries] for fid, entries in shown.items()} == expected_shown
    assert omitted == expected_omitted
    assert by_agent == expected_by_agent
    assert sum(1 for entries in shown.values() for entry in entries if entry["by_agent"]) == by_agent
    # Who and where, for the selected rows.
    hosts = {h.id: h.ip_address for h in (a, b, c, d, e)}
    records = {r.id: r for r in db.query(EvidenceRecord).all()}
    for entries in shown.values():
        for entry in entries:
            record = records[entry["id"]]
            assert entry["host"] == hosts[record.host_id]
            assert entry["by"] == ("Test Admin" if record.agent_session_id or record.recorded_by_user_id else None)
    # The bound: the database hands over only the rows that are printed —
    # not every matching record (31 here, of which 23 are shown).
    if db.get_bind().dialect.name == "postgresql":
        assert sum(reads.rowcounts) == 2 * CAP + 3, reads.rowcounts
        assert len(reads.rowcounts) == 1


def test_no_matching_record_is_still_nothing(db_session, test_project):
    a = _host(db_session, test_project, "10.91.0.1")
    f = _finding(db_session, test_project, "Unconfirmed", hosts=[a])
    _record(db_session, test_project, f, a, run=1, outcome="inconclusive")
    db_session.commit()
    service = ClientReportService(db_session)
    assert service._confirmations(service._included(test_project.id)) == ({}, {}, 0)
    assert service._confirmations([]) == ({}, {}, 0)
