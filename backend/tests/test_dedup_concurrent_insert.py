"""2.374.4 review H2: a concurrent insert of the same host/port is recovered
inside the savepoint and leaves the parent transaction usable.

``begin_nested()`` flushes what is pending.  The new row used to be added
BEFORE it, so the losing INSERT ran in that flush — outside the ``try`` — and
the IntegrityError escaped with the parent transaction invalidated.  Here the
"other writer" commits its row right after our lookup found nothing.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, insert

from app.db import models
from app.services.host_deduplication_service import HostDeduplicationService
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(not USING_POSTGRES, reason="unique-violation recovery is Postgres behaviour")


def _other_writer_after_first_lookup(db_session, entity, write):
    """After the first SELECT of ``entity`` runs, perform ``write``."""
    fired = []

    def hook(state):
        if fired or not state.is_select:
            return None
        if entity not in {d.get("entity") for d in state.statement.column_descriptions}:
            return None
        fired.append(True)
        result = state.invoke_statement()
        write(state.session.connection())
        return result

    event.listen(db_session, "do_orm_execute", hook)
    return fired, lambda: event.remove(db_session, "do_orm_execute", hook)


def _scan(db_session, project):
    scan = models.Scan(project_id=project.id, filename="a.xml", tool_name="nmap", scan_type="port_scan")
    db_session.add(scan)
    db_session.flush()
    return scan


def test_a_host_inserted_concurrently_is_resolved_and_the_transaction_survives(db_session, test_project):
    scan = _scan(db_session, test_project)
    now = datetime.now(timezone.utc)
    fired, done = _other_writer_after_first_lookup(
        db_session, models.Host,
        lambda conn: conn.execute(insert(models.Host).values(
            project_id=test_project.id, ip_address="10.77.0.1", state="up", first_seen=now, last_seen=now,
        )),
    )
    try:
        host = HostDeduplicationService(db_session).find_or_create_host(
            "10.77.0.1", scan.id, {"state": "up"}, project_id=test_project.id)
    finally:
        done()
    assert fired
    assert host.ip_address == "10.77.0.1"
    # The parent transaction is intact: the scan written before is still there.
    db_session.commit()
    assert db_session.query(models.Host).filter_by(project_id=test_project.id, ip_address="10.77.0.1").count() == 1
    assert db_session.get(models.Scan, scan.id) is not None


def test_a_port_inserted_concurrently_is_resolved_and_the_transaction_survives(db_session, test_project):
    scan = _scan(db_session, test_project)
    # The host exists from an EARLIER import: a host this parse created is
    # invisible to other writers until commit, so only a known host can race.
    host = HostDeduplicationService(db_session).find_or_create_host(
        "10.77.0.2", scan.id, {"state": "up"}, project_id=test_project.id)
    db_session.commit()
    service = HostDeduplicationService(db_session)
    fired, done = _other_writer_after_first_lookup(
        db_session, models.Port,
        lambda conn: conn.execute(insert(models.Port).values(
            host_id=host.id, port_number=443, protocol="tcp", state="open", is_active=True,
        )),
    )
    try:
        port = service.find_or_create_port(host.id, scan.id, {"port_number": 443, "protocol": "tcp", "state": "open"})
    finally:
        done()
    assert fired
    assert port.port_number == 443
    db_session.commit()
    assert db_session.query(models.Port).filter_by(host_id=host.id, port_number=443).count() == 1
