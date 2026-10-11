"""A CIDR is in a scope once (review 2026-10-10, H22).

Both add paths read the scope's CIDRs and then inserted, with nothing in
between: a double-submitted "add subnets", or a person and an agent uploading
one list, stored the CIDR twice — every host then mapped to both rows and the
segment counts doubled.  ``uq_subnets_scope_cidr`` (revision ``a4f8c2e6b0d1``)
removes the hazard; these tests pin the constraint and what each route answers
when it LOSES the race, with two really-committing sessions: A holds the
uncommitted row, B's request reads (sees nothing), inserts, waits on A, and
after A's commit must treat the CIDR as already there.

Before the constraint B did not wait at all and both rows were stored.
"""
from __future__ import annotations

import asyncio
import io
import threading
import time

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints import scopes as scopes_routes
from app.db.models import Subnet
from app.schemas.schemas import SubnetBatchCreate, SubnetUpdate
from app.services.default_scope import get_or_create_default_scope
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


def test_the_database_refuses_a_second_row_for_one_cidr_in_a_scope(db_session, test_project):
    scope = get_or_create_default_scope(db_session, test_project.id)
    db_session.add(Subnet(scope_id=scope.id, cidr="10.60.0.0/24"))
    db_session.flush()
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.add(Subnet(scope_id=scope.id, cidr="10.60.0.0/24"))
            db_session.flush()
    assert "uq_subnets_scope_cidr" in str(refused.value.orig)


def test_adding_a_cidr_the_scope_holds_is_still_a_400(client, db_session, test_project):
    """The ordinary (not raced) duplicate keeps its answer."""
    url = f"/api/v1/projects/{test_project.id}/scopes/subnets"
    assert client.post(url, json={"subnets": [{"cidr": "10.61.0.0/24"}]}).status_code == 201
    again = client.post(url, json={"subnets": [{"cidr": "10.61.0.5/24"}]})   # same network
    assert again.status_code == 400 and "10.61.0.0/24 already exists" in again.json()["detail"]


# ---------------------------------------------------------------------------
# The race, with two connections
# ---------------------------------------------------------------------------

def _race_setup(pair, *cidrs_a_holds: str):
    """A committed project with its scope, and session A holding ``cidrs`` as
    flushed, UNCOMMITTED subnets.  Returns (project, user, scope_id)."""
    project, user = pair.project()
    scope_id = pair.commit(lambda db: get_or_create_default_scope(db, project.id, user_id=user.id).id)
    for cidr in cidrs_a_holds:
        pair.a.add(Subnet(scope_id=scope_id, cidr=cidr))
    pair.a.flush()
    return project, user, scope_id


def _run_b_while_a_holds(pair, request):
    """Start ``request(pair.b)`` on a thread, wait until it is blocked on A's
    row, commit A, and return {"result": …} or {"error": …}."""
    outcome: dict = {}

    def b():
        try:
            outcome["result"] = request(pair.b)
            pair.b.commit()
        except BaseException as exc:  # noqa: BLE001 — handed back to the test
            pair.b.rollback()
            outcome["error"] = exc

    thread = threading.Thread(target=b, daemon=True)
    thread.start()
    deadline = time.time() + 10
    waiting = False
    while time.time() < deadline and not waiting and thread.is_alive():
        with pair._engine.connect() as probe:
            waiting = bool(probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar())
        time.sleep(0.05)
    assert waiting and thread.is_alive(), f"B did not wait for A's uncommitted subnet: {outcome}"
    pair.a.commit()
    thread.join(20)
    assert not thread.is_alive(), "B never finished after A committed"
    return outcome


def _cidrs(pair, scope_id: int) -> list:
    db = pair.fresh()
    try:
        return sorted(c for (c,) in db.query(Subnet.cidr).filter(Subnet.scope_id == scope_id))
    finally:
        db.close()


def test_a_raced_add_is_told_the_cidr_exists_and_nothing_is_stored_twice(two_sessions):  # noqa: F811
    project, user, scope_id = _race_setup(two_sessions, "10.62.0.0/24")

    outcome = _run_b_while_a_holds(two_sessions, lambda db: scopes_routes.add_subnets(
        SubnetBatchCreate(subnets=[{"cidr": "10.62.1.0/24"}, {"cidr": "10.62.0.0/24"}]),
        db=db, project=project, current_user=user,
    ))

    refused = outcome.get("error")
    assert isinstance(refused, HTTPException), outcome
    assert refused.status_code == 400
    assert refused.detail == "CIDR 10.62.0.0/24 already exists in this scope"
    # All or nothing, as the add form always was: B's other CIDR is not stored.
    assert _cidrs(two_sessions, scope_id) == ["10.62.0.0/24"]


def test_a_raced_upload_skips_the_cidr_the_other_request_added(two_sessions):  # noqa: F811
    project, user, scope_id = _race_setup(two_sessions, "10.63.0.0/24")
    content = b"subnet,labels,description,site\n10.63.0.0/24,prod,Raced,\n10.63.1.0/24,lab,,\n"

    def upload(db):
        return asyncio.run(scopes_routes.upload_subnet_file(
            file=UploadFile(filename="scope.csv", file=io.BytesIO(content)),
            db=db, current_user=user, project=project,
        ))

    outcome = _run_b_while_a_holds(two_sessions, upload)

    assert "error" not in outcome, outcome
    answer = outcome["result"]
    # The CIDR A added is one the scope already holds: skipped, like any
    # duplicate — and what the file says about it is still applied to it.
    assert answer.subnets_added == 1
    assert "1 duplicate skipped" in answer.message
    assert _cidrs(two_sessions, scope_id) == ["10.63.0.0/24", "10.63.1.0/24"]
    db = two_sessions.fresh()
    try:
        raced = db.query(Subnet).filter(Subnet.scope_id == scope_id, Subnet.cidr == "10.63.0.0/24").one()
        assert raced.description == "Raced"
        assert [label.name for label in raced.labels] == ["prod"]
    finally:
        db.close()


def test_a_raced_edit_to_a_cidr_the_other_request_added_is_a_400(two_sessions):  # noqa: F811
    project, user = two_sessions.project()
    scope_id = two_sessions.commit(
        lambda db: get_or_create_default_scope(db, project.id, user_id=user.id).id
    )
    mine = two_sessions.commit(lambda db: _add(db, scope_id, "10.64.9.0/24"))
    two_sessions.a.add(Subnet(scope_id=scope_id, cidr="10.64.0.0/24"))
    two_sessions.a.flush()

    outcome = _run_b_while_a_holds(two_sessions, lambda db: scopes_routes.update_subnet(
        mine.id, SubnetUpdate(cidr="10.64.0.0/24"), db=db, project=project,
    ))

    refused = outcome.get("error")
    assert isinstance(refused, HTTPException), outcome
    assert refused.status_code == 400
    assert refused.detail == "CIDR 10.64.0.0/24 already exists in this scope"
    assert _cidrs(two_sessions, scope_id) == ["10.64.0.0/24", "10.64.9.0/24"]


def _add(db, scope_id: int, cidr: str) -> Subnet:
    row = Subnet(scope_id=scope_id, cidr=cidr)
    db.add(row)
    db.flush()
    return row
