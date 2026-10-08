"""Remediation writes from two connections at once (review 2026-10-07)."""
import threading
import time
from types import SimpleNamespace

from sqlalchemy import text

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_remediation import FindingHostRemediation, RemediationEvent
from app.schemas.remediation_schemas import ApplyBody, NoteCreate
from app.services import remediation_service
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


def _estate(db, project_id):
    host = models.Host(project_id=project_id, ip_address="10.77.0.1", state="up")
    finding = Finding(project_id=project_id, title="SMB signing", severity="high",
                      status="confirmed", source="manual")
    db.add_all([host, finding])
    db.flush()
    link = FindingHost(finding_id=finding.id, host_id=host.id, host_status="open")
    db.add(link)
    db.flush()
    return host.id, link.id


def test_a_retry_sent_while_the_first_note_is_in_flight_gets_that_note(two_sessions):  # noqa: F811
    """A adds a keyed note and has not committed.  B, the retry, sees no
    stored note, inserts, and waits on the unique index; when A commits B is
    refused by it.  That used to be a 500 — it is the stored note."""
    project, user = two_sessions.project()
    host_id, _ = two_sessions.commit(lambda db: _estate(db, project.id))
    who = SimpleNamespace(user_id=user.id, session=None)
    body = NoteCreate(host_id=host_id, body="Emailed the report", request_key="sheet-7")

    first, created = remediation_service.add_note(two_sessions.a, project.id, body, who)
    assert created
    first_id = first.id

    outcome = {}

    def retry():
        try:
            row, made = remediation_service.add_note(two_sessions.b, project.id, body, who)
            outcome["id"], outcome["created"] = row.id, made
            two_sessions.b.commit()
        except BaseException as exc:  # noqa: BLE001
            two_sessions.b.rollback()
            outcome["error"] = exc

    thread = threading.Thread(target=retry, daemon=True)
    thread.start()
    deadline = time.time() + 10
    waiting = False
    while time.time() < deadline and not waiting and thread.is_alive():
        with two_sessions._engine.connect() as probe:
            waiting = bool(probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar())
        time.sleep(0.05)
    assert waiting and thread.is_alive(), f"the retry did not wait for the first insert: {outcome}"

    two_sessions.a.commit()
    thread.join(20)
    assert not thread.is_alive()
    assert outcome == {"id": first_id, "created": False}, outcome

    db = two_sessions.fresh()
    try:
        assert db.query(RemediationEvent).filter_by(project_id=project.id).count() == 1
    finally:
        db.close()


def test_two_first_writes_of_one_finding_on_a_host_make_one_record(two_sessions):  # noqa: F811
    project, user = two_sessions.project()
    _, link_id = two_sessions.commit(lambda db: _estate(db, project.id))
    who = SimpleNamespace(user_id=user.id, session=None)

    def write(contact):
        request = ApplyBody(rows=[{"finding_host_id": link_id, "contact_email": contact}], overwrite=True)
        return lambda db: remediation_service.apply(db, project.id, request, who)["summary"]["changed"]

    results = two_sessions.race(write("a@client.example"), write("b@client.example"))
    assert not [r for r in results if isinstance(r, BaseException)], results

    db = two_sessions.fresh()
    try:
        rows = db.query(FindingHostRemediation).filter_by(finding_host_id=link_id).all()
        assert len(rows) == 1 and rows[0].contact_email in {"a@client.example", "b@client.example"}
    finally:
        db.close()
