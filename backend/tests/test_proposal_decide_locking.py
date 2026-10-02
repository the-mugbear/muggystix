"""Two reviewers accepting two drafts of one field (review 2026-10-02 H1).

Deciding locked only the proposal row.  Accepting a report-text proposal then
wrote the finding and superseded the field's other pending drafts: reviewer A
held proposal A, reviewer B held proposal B, A wrote the finding, B waited on
the finding, and A's supersede waited on proposal B — Postgres broke the cycle
with ``deadlock detected`` and one request answered 500.

``proposal_service.lock_for_decision`` now locks the FINDING before the
proposal, so the second reviewer waits there and then reads their draft as
superseded: a 409.

Two REAL connections (``tests/two_connections.py``); the ordinary fixtures
bind every session to one connection, where nothing ever waits.
"""
import threading

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import proposals as routes
from app.db.models_findings import Finding
from app.db.models_proposals import AgentProposal
from app.services import proposal_service as proposals
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


def _two_drafts(two_sessions, project, user, fields=("description", "description")):
    def seed(db):
        f = Finding(project_id=project.id, title="Race", severity="high", status="open",
                    source="manual", created_by_id=user.id, description="original")
        db.add(f)
        db.flush()
        rows = [
            AgentProposal(project_id=project.id, finding_id=f.id, kind="finding_text",
                          source="agent", field=field, payload={"value": value})
            for field, value in zip(fields, ("draft A", "draft B"))
        ]
        db.add_all(rows)
        db.flush()
        return f.id, [p.id for p in rows]
    return two_sessions.commit(seed)


def _hold_both_before_the_change(monkeypatch):
    """Make each decision wait, after it has taken its locks and before it
    changes anything, until the other has got as far — or 3 s have passed.

    On the old lock order both get there (each holds its own proposal), both
    go on, and they deadlock.  On the new one only the first gets there: the
    other is waiting for the finding, so the first gives up waiting and goes
    on alone."""
    both_locked = threading.Barrier(2)
    real = {"accept": proposals.accept_proposal, "reject": proposals.reject_proposal}

    def held(name):
        def run(*args, **kwargs):
            try:
                both_locked.wait(timeout=3)
            except threading.BrokenBarrierError:
                pass
            return real[name](*args, **kwargs)
        return run

    monkeypatch.setattr(proposals, "accept_proposal", held("accept"))
    monkeypatch.setattr(proposals, "reject_proposal", held("reject"))


def _decide(project, user, proposal_id, action):
    def run(db):
        return routes._decide(db, project.id, proposal_id, user, action, routes.DecideBody(), serialize=False)
    return run


def test_two_accepts_of_one_fields_drafts_end_as_one_accept_and_one_409(two_sessions, monkeypatch):
    project, user = two_sessions.project()
    finding_id, ids = _two_drafts(two_sessions, project, user)
    _hold_both_before_the_change(monkeypatch)

    results = two_sessions.race(
        _decide(project, user, ids[0], "accept"), _decide(project, user, ids[1], "accept"),
    )

    refused = [r for r in results if isinstance(r, BaseException)]
    assert len(refused) == 1, results                    # exactly one went through
    assert isinstance(refused[0], HTTPException), repr(refused[0])     # and no database error
    assert refused[0].status_code == 409 and "already superseded" in refused[0].detail

    winner = ids[1 - results.index(refused[0])]
    loser = ids[results.index(refused[0])]
    db = two_sessions.fresh()
    try:
        assert db.get(AgentProposal, winner).status == "accepted"
        assert db.get(AgentProposal, loser).status == "superseded"
        assert db.get(AgentProposal, loser).decided_by_user_id is None
        assert db.get(Finding, finding_id).description == ("draft A" if winner == ids[0] else "draft B")
    finally:
        db.close()


def test_an_accept_and_a_reject_of_one_fields_drafts_never_fail_in_the_database(two_sessions, monkeypatch):
    """The reject takes the same locks in the same order.  Whichever goes
    first, nothing errors: the accept always lands, and the reject either
    lands first or finds its draft superseded (409)."""
    project, user = two_sessions.project()
    finding_id, ids = _two_drafts(two_sessions, project, user)
    _hold_both_before_the_change(monkeypatch)

    accepted, rejected = two_sessions.race(
        _decide(project, user, ids[0], "accept"), _decide(project, user, ids[1], "reject"),
    )

    assert not isinstance(accepted, BaseException), repr(accepted)
    if isinstance(rejected, BaseException):
        assert isinstance(rejected, HTTPException) and rejected.status_code == 409, repr(rejected)
    db = two_sessions.fresh()
    try:
        assert db.get(AgentProposal, ids[0]).status == "accepted"
        assert db.get(AgentProposal, ids[1]).status in ("rejected", "superseded")
        assert db.get(Finding, finding_id).description == "draft A"
    finally:
        db.close()


def test_accepts_of_different_fields_of_one_finding_both_land(two_sessions, monkeypatch):
    """The finding lock serialises them; neither supersedes the other."""
    project, user = two_sessions.project()
    finding_id, ids = _two_drafts(two_sessions, project, user, fields=("description", "impact"))
    _hold_both_before_the_change(monkeypatch)

    results = two_sessions.race(
        _decide(project, user, ids[0], "accept"), _decide(project, user, ids[1], "accept"),
    )

    assert not any(isinstance(r, BaseException) for r in results), results
    db = two_sessions.fresh()
    try:
        finding = db.get(Finding, finding_id)
        assert (finding.description, finding.impact) == ("draft A", "draft B")
        assert {db.get(AgentProposal, i).status for i in ids} == {"accepted"}
    finally:
        db.close()


def test_a_decision_locks_the_finding_only_for_report_text(db_session, test_project, test_user):
    """Other kinds keep their own lock order (a promotion: evidence first,
    then the finding) — the decide path must not take the finding ahead of it."""
    from sqlalchemy import event

    f = Finding(project_id=test_project.id, title="t", severity="high", status="open",
                source="manual", created_by_id=test_user.id)
    db_session.add(f)
    db_session.flush()
    text = AgentProposal(project_id=test_project.id, finding_id=f.id, kind="finding_text",
                         source="agent", field="impact", payload={"value": "x"})
    endpoint = AgentProposal(project_id=test_project.id, finding_id=f.id, kind="endpoint_status",
                             source="agent", payload={"host_status": "remediated"})
    db_session.add_all([text, endpoint])
    db_session.commit()

    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "FOR " in statement:
            seen.append(" ".join(statement.split()))

    engine = db_session.get_bind().engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        proposals.lock_for_decision(db_session, test_project.id, text.id)
        of_text, seen[:] = list(seen), []
        proposals.lock_for_decision(db_session, test_project.id, endpoint.id)
        of_endpoint = list(seen)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    if engine.dialect.name == "postgresql":
        assert len(of_text) == 2
        assert "FROM findings" in of_text[0] and of_text[0].endswith("FOR NO KEY UPDATE")
        assert "FROM agent_proposals" in of_text[1] and of_text[1].endswith("FOR UPDATE")
        assert len(of_endpoint) == 1 and "FROM agent_proposals" in of_endpoint[0]
    with pytest.raises(HTTPException) as exc:
        proposals.lock_for_decision(db_session, test_project.id, 999_999_999)
    assert exc.value.status_code == 404
